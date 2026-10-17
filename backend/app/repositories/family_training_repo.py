"""Atomic monotonic training attempts and bounded UTC cohort aggregates."""

from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.family_training import FamilyTrainingEvent


class FamilyTrainingRepository:
    """Keep owner exclusion and concurrent snapshot validation in one SQL write."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_progress(
        self, project_id: UUID, event: FamilyTrainingEvent, identity: UUID | None, today: date
    ) -> None:
        """Insert an observed attempt or extend its immutable answer prefix atomically."""
        await self.db.execute(
            text("""
                INSERT INTO family_training_attempts AS attempt
                    (project_id, attempt_id, first_seen_day, curriculum_version,
                     lang, answers, completed)
                SELECT p.id, :attempt_id, :today, :curriculum_version,
                       :lang, CAST(:answers AS boolean[]), :completed
                FROM projects p JOIN agents a ON a.id = p.creator_agent_id
                WHERE p.id = :project AND p.metrics_profile = 'family_safety'
                  AND (CAST(:identity AS uuid) IS NULL
                       OR a.owner_user_id IS DISTINCT FROM CAST(:identity AS uuid))
                ON CONFLICT (project_id, attempt_id) DO UPDATE
                SET answers = EXCLUDED.answers, completed = EXCLUDED.completed
                WHERE NOT attempt.completed
                  AND attempt.curriculum_version = EXCLUDED.curriculum_version
                  AND attempt.lang = EXCLUDED.lang
                  AND cardinality(EXCLUDED.answers) >= cardinality(attempt.answers)
                  AND (cardinality(attempt.answers) = 0
                       OR EXCLUDED.answers[1:cardinality(attempt.answers)] = attempt.answers)
                  AND (cardinality(EXCLUDED.answers) > cardinality(attempt.answers)
                       OR EXCLUDED.completed)
            """),
            {
                **event.model_dump(exclude={"event"}),
                "project": project_id,
                "identity": identity,
                "today": today,
            },
        )

    async def aggregate(self, project_id: UUID, version: int, today: date) -> list[RowMapping]:
        """Return ten ordered rounds and totals from the inclusive UTC first-seen cohort."""
        result = await self.db.execute(
            text("""
                WITH cohort AS MATERIALIZED (
                    SELECT answers, completed FROM family_training_attempts
                    WHERE project_id = :project AND curriculum_version = :version
                      AND first_seen_day BETWEEN :start AND :today
                ), totals AS (
                    SELECT count(*) AS starts,
                           count(*) FILTER (WHERE completed) AS completions FROM cohort
                )
                SELECT round_id, totals.starts, totals.completions,
                       count(c.answers[round_id]) AS answered,
                       count(*) FILTER (WHERE c.answers[round_id] IS FALSE) AS wrong
                FROM generate_series(1, 10) AS rounds(round_id)
                CROSS JOIN totals LEFT JOIN cohort c ON TRUE
                GROUP BY round_id, totals.starts, totals.completions ORDER BY round_id
            """),
            {
                "project": project_id,
                "version": version,
                "start": today - timedelta(days=6),
                "today": today,
            },
        )
        return list(result.mappings())

    async def purge_expired(self, today: date) -> int:
        """Delete attempts older than the eight retained UTC calendar days."""
        count = await self.db.scalar(
            text("""
                WITH deleted AS (
                    DELETE FROM family_training_attempts WHERE first_seen_day < :oldest
                    RETURNING 1
                ) SELECT count(*) FROM deleted
            """),
            {"oldest": today - timedelta(days=7)},
        )
        return int(count or 0)
