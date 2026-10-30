"""Atomic daily visit storage and project-scoped UTC cohort queries."""

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.usage import (
    ProjectUsage,
    ServiceEvent,
    UsageD7,
    UsageMeasurement,
    UsageMetrics,
    UsageReport,
    UsageVisit,
)


class UsageRepository:
    """Keep visit deduplication and owner exclusion in the caller's transaction."""

    _AGGREGATES = text("""
        WITH scoped_projects AS MATERIALIZED (
            SELECT id FROM projects
            WHERE (CAST(:project_id AS uuid) IS NOT NULL AND id = CAST(:project_id AS uuid))
               OR (CAST(:agent_id AS uuid) IS NOT NULL
                   AND creator_agent_id = CAST(:agent_id AS uuid))
        ), visitor_days AS (
            SELECT v.project_id, v.visitor_id,
                   bool_or(v.visit_day = :today) AS visited_today,
                   bool_or(v.visit_day >= :week_start) AS visited_week,
                   bool_or(v.visit_day = :cohort_day) AS visited_cohort
            FROM service_visits v JOIN scoped_projects p ON p.id = v.project_id
            WHERE v.visit_day BETWEEN :cohort_day AND :today
            GROUP BY v.project_id, v.visitor_id
        )
        SELECT p.id AS project_id,
               count(v.visitor_id) FILTER (WHERE v.visited_today) AS uniques_1d,
               count(v.visitor_id) FILTER (WHERE v.visited_week) AS uniques_7d,
               count(v.visitor_id) FILTER (WHERE v.visited_cohort) AS observed_cohort_size,
               count(v.visitor_id) FILTER (WHERE v.visited_cohort AND v.visited_today)
                   AS observed_returned_count,
               COALESCE(
                   count(v.visitor_id) FILTER (WHERE v.visited_cohort AND v.visited_today)
                   ::double precision /
                   NULLIF(count(v.visitor_id) FILTER (WHERE v.visited_cohort), 0),
                   0.0
               ) AS retention_d7
        FROM scoped_projects p LEFT JOIN visitor_days v ON v.project_id = p.id
        GROUP BY p.id ORDER BY p.id
    """)

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_visit(self, project_id: UUID, visit: UsageVisit, today: date) -> bool:
        """Insert one project/visitor/day, excluding its verified owner atomically."""
        inserted = await self.db.scalar(
            text("""
                INSERT INTO service_visits (project_id, visitor_id, visit_day)
                SELECT p.id, :visitor_id, :today
                FROM projects p JOIN agents a ON a.id = p.creator_agent_id
                WHERE p.id = :project_id
                  AND (CAST(:verified_user_id AS uuid) IS NULL OR a.owner_user_id IS NULL
                       OR a.owner_user_id <> CAST(:verified_user_id AS uuid))
                ON CONFLICT (project_id, visitor_id, visit_day) DO NOTHING
                RETURNING project_id
            """),
            {
                "project_id": project_id,
                "visitor_id": visit.visitor_id,
                "today": today,
                "verified_user_id": visit.verified_user_id,
            },
        )
        return inserted is not None

    async def record_action_receipt(
        self, project_id: UUID, receipt: bytes, identity: UUID | None, today: date
    ) -> None:
        """Deduplicate delivery and exclude the verified owner in one atomic statement."""
        await self.db.execute(
            text("""
                INSERT INTO service_action_receipts (project_id, action_day, action, receipt_hash)
                SELECT p.id, :today, 'board_created', :receipt
                FROM projects p JOIN agents a ON a.id = p.creator_agent_id
                WHERE p.id = :project_id
                  AND (CAST(:identity AS uuid) IS NULL OR a.owner_user_id IS NULL
                       OR a.owner_user_id <> CAST(:identity AS uuid))
                ON CONFLICT (project_id, receipt_hash) DO NOTHING
            """),
            {"project_id": project_id, "today": today, "receipt": receipt, "identity": identity},
        )

    async def _usage_rows(
        self,
        today: date,
        *,
        project_id: UUID | None = None,
        agent_id: UUID | None = None,
    ) -> list[RowMapping]:
        rows = await self.db.execute(
            self._AGGREGATES,
            {
                "project_id": project_id,
                "agent_id": agent_id,
                "today": today,
                "week_start": today - timedelta(days=6),
                "cohort_day": today - timedelta(days=7),
            },
        )
        return list(rows.mappings())

    async def get_usage(self, project_id: UUID, today: date) -> UsageMetrics | None:
        """Return an existing project's aggregate, or None for an unknown project."""
        rows = await self._usage_rows(today, project_id=project_id)
        return UsageMetrics.model_validate(rows[0]) if rows else None

    async def get_usage_report(self, project_id: UUID, today: date) -> UsageReport | None:
        """Read the legacy numbers and cohort context from one shared aggregate query."""
        rows = await self._usage_rows(today, project_id=project_id)
        if not rows:
            return None
        row = rows[0]
        cohort, returned = row["observed_cohort_size"], row["observed_returned_count"]
        return UsageReport(
            **UsageMetrics.model_validate(row).model_dump(),
            measurement=UsageMeasurement(
                as_of_day=today,
                d7=UsageD7(
                    cohort_day=today - timedelta(days=7),
                    observed_cohort_size=cohort,
                    observed_returned_count=returned,
                    value=returned / cohort if cohort else None,
                ),
            ),
        )

    async def get_agent_usage(self, agent_id: UUID, today: date) -> list[ProjectUsage]:
        """Read all projects of this agent in one query, without a project limit."""
        rows = await self._usage_rows(today, agent_id=agent_id)
        return [ProjectUsage.model_validate(row) for row in rows]

    async def purge_expired(self, today: date) -> int:
        """Purge visits and action records, returning only the deleted visit count."""
        count = await self.db.scalar(
            text(
                "WITH deleted AS (DELETE FROM service_visits WHERE visit_day < :oldest "
                "RETURNING 1) SELECT count(*) FROM deleted"
            ),
            {"oldest": today - timedelta(days=7)},
        )
        await self.db.execute(
            text("DELETE FROM service_action_receipts WHERE action_day < :oldest"),
            {"oldest": today - timedelta(days=7)},
        )
        return int(count or 0)

    async def record_event(
        self, project_id: UUID, event: ServiceEvent, identity: UUID | None, today: date
    ) -> None:
        """Atomically increment one daily action, excluding the verified project owner."""
        await self.db.execute(
            text("""
            INSERT INTO service_event_counts (project_id,event_day,event,model,lang,count)
            SELECT p.id,:today,:event,:model,:lang,1
            FROM projects p JOIN agents a ON a.id=p.creator_agent_id
            WHERE p.id=:project AND (CAST(:identity AS uuid) IS NULL
                OR a.owner_user_id IS NULL OR a.owner_user_id<>CAST(:identity AS uuid))
            ON CONFLICT (project_id,event_day,event,model,lang)
            DO UPDATE SET count=service_event_counts.count+1
        """),
            {**event.model_dump(), "project": project_id, "identity": identity, "today": today},
        )

    async def project_creator(self, project_id: UUID) -> RowMapping | None:
        """Read the creator before any creator-only event aggregate."""
        result = await self.db.execute(
            text("SELECT creator_agent_id FROM projects WHERE id=:id"),
            {"id": project_id},
        )
        return result.mappings().first()

    async def event_groups(self, project_id: UUID, today: date) -> list[RowMapping]:
        """Aggregate the inclusive seven-day UTC window without retaining future dates."""
        rows = await self.db.execute(
            text("""
            SELECT model,lang,
              COALESCE(sum(count) FILTER (WHERE event='form_open'),0) AS form_open,
              COALESCE(sum(count) FILTER (WHERE event='png_download'),0) AS png_download,
              COALESCE(sum(count) FILTER (WHERE event='png_download'),0)::double precision /
                NULLIF(sum(count) FILTER (WHERE event='form_open'),0) AS download_open_ratio
            FROM service_event_counts WHERE project_id=:project
              AND event_day BETWEEN :start AND :today
            GROUP BY model,lang ORDER BY model,lang
        """),
            {"project": project_id, "start": today - timedelta(days=6), "today": today},
        )
        return list(rows.mappings())
