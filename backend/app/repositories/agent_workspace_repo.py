"""Read-only SQL projections with one shared owner authorization predicate."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AgentWorkspaceRepository:
    """Bind owner-scoped projections to a single request-owned session."""

    task_from = """
        FROM tasks t
        LEFT JOIN task_reviews r ON r.task_id = t.id
        LEFT JOIN agents requester ON requester.id = COALESCE(r.requester_agent_id,
            t.created_by_agent_id)
        LEFT JOIN agents claimant ON claimant.id = t.claimed_by_agent_id
        LEFT JOIN projects p ON p.id = t.project_id
        LEFT JOIN learning_budget_reservations b ON b.task_id = t.id
        WHERE (requester.owner_user_id = :owner OR claimant.owner_user_id = :owner)
    """
    task_columns = """
        t.id, t.title, t.description, t.status AS task_status, t.created_at,
        r.state AS review_state, r.version AS review_version, r.relationship,
        CASE WHEN p.id IS NOT NULL THEN jsonb_build_object('id', p.id,
            'title', p.title) END AS project,
        CASE WHEN requester.id IS NOT NULL THEN jsonb_build_object('id', requester.id,
            'name', requester.name, 'handle', requester.handle) END AS requester,
        CASE WHEN claimant.id IS NOT NULL THEN jsonb_build_object('id', claimant.id,
            'name', claimant.name, 'handle', claimant.handle) END AS claimant,
        array_remove(ARRAY[
            CASE WHEN requester.owner_user_id = :owner THEN 'requester' END,
            CASE WHEN claimant.owner_user_id = :owner THEN 'claimant' END], NULL) AS viewer_roles,
        CASE WHEN b.task_id IS NOT NULL THEN jsonb_build_object('amount', b.amount,
            'status', b.status) END AS learning_reservation
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def agents(self, owner: UUID) -> list[dict]:
        """Aggregate distinct reservations without joining event rows or creating accounts."""
        rows = await self.db.execute(
            text("""
            WITH owned AS (SELECT id, name, handle, specialization, is_active,
                created_at, last_heartbeat FROM agents WHERE owner_user_id = :owner
                AND is_hosted = FALSE),
            settlements AS (
                SELECT b.requester_agent_id AS agent_id,
                    SUM(b.amount) FILTER (WHERE b.status = 'captured') AS transferred,
                    SUM(b.amount) FILTER (WHERE b.status = 'refunded') AS refunded,
                    0::bigint AS received
                FROM learning_budget_reservations b JOIN owned a ON a.id = b.requester_agent_id
                GROUP BY b.requester_agent_id
                UNION ALL
                SELECT t.claimed_by_agent_id, 0, 0, SUM(b.amount)
                FROM learning_budget_reservations b JOIN tasks t ON t.id = b.task_id
                JOIN owned a ON a.id = t.claimed_by_agent_id
                WHERE b.status = 'captured' GROUP BY t.claimed_by_agent_id),
            totals AS (SELECT agent_id, SUM(transferred) AS transferred,
                SUM(refunded) AS refunded, SUM(received) AS received
                FROM settlements GROUP BY agent_id)
            SELECT a.id, a.name, a.handle, COALESCE(a.specialization, '') AS specialization,
                COALESCE(a.is_active, FALSE) AS is_active, a.created_at, a.last_heartbeat,
                jsonb_build_object('available', COALESCE(b.available, 0),
                    'reserved', COALESCE(b.reserved, 0),
                    'initial_grant_applied', COALESCE(b.initial_grant_applied, FALSE),
                    'transferred', COALESCE(s.transferred, 0),
                    'received', COALESCE(s.received, 0),
                    'refunded', COALESCE(s.refunded, 0)) AS budget
            FROM owned a LEFT JOIN learning_budget_accounts b ON b.agent_id = a.id
            LEFT JOIN totals s ON s.agent_id = a.id ORDER BY a.created_at DESC, a.id DESC
        """),
            {"owner": owner},
        )
        return [dict(row) for row in rows.mappings()]

    async def review_counts(self, owner: UUID) -> dict:
        """Count visible review states, excluding legacy tasks and duplicate owner roles."""
        rows = await self.db.execute(
            text(
                "SELECT r.state, COUNT(*) AS count "
                + self.task_from
                + " AND r.task_id IS NOT NULL GROUP BY r.state"
            ),
            {"owner": owner},
        )
        return {row["state"]: row["count"] for row in rows.mappings()}

    async def tasks(self, owner: UUID, limit: int, offset: int) -> tuple[list[dict], int]:
        """Page metadata without projecting private results."""
        total = await self.db.scalar(text("SELECT COUNT(*) " + self.task_from), {"owner": owner})
        rows = await self.db.execute(
            text(
                "SELECT "
                + self.task_columns
                + self.task_from
                + " ORDER BY t.created_at DESC, t.id DESC LIMIT :limit OFFSET :offset"
            ),
            {"owner": owner, "limit": limit, "offset": offset},
        )
        return [dict(row) for row in rows.mappings()], int(total or 0)

    async def detail(self, owner: UUID, task: UUID) -> dict | None:
        """Apply identical privacy rules to known and unknown task identifiers."""
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT "
                        + self.task_columns
                        + ", CASE WHEN r.task_id IS NOT NULL THEN r.result "
                        "ELSE t.result END AS result "
                        + self.task_from
                        + " AND t.id = :task"
                    ),
                    {"owner": owner, "task": task},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def history(self, owner: UUID, task: UUID) -> list[dict]:
        """Reapply owner authorization before reading event content."""
        rows = await self.db.execute(
            text(
                """
            SELECT jsonb_build_object('id', actor.id, 'name', actor.name,
                'handle', actor.handle) AS actor, e.action, e.payload->>'reason' AS reason,
                e.response->>'state' AS state, (e.response->>'version')::integer AS version,
                e.created_at
            FROM task_review_events e JOIN agents actor ON actor.id = e.actor_agent_id
            WHERE e.task_id = :task AND EXISTS (SELECT 1
        """
                + self.task_from
                + " AND t.id = e.task_id) ORDER BY e.id DESC LIMIT 50"
            ),
            {"owner": owner, "task": task},
        )
        return [dict(row) for row in rows.mappings()]
