"""Shared durable code/file rewards, serialized in the caller transaction."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class CodeKarmaRepository:
    """Keep the weekly reservation and agent mutation on one locked session."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def award(self, agent_id: UUID, points: int, commits: int = 0) -> int:
        """Award at most the remaining ISO-week budget; count every accepted commit."""
        if points < 0 or commits < 0:
            raise ValueError("Code rewards and commit counts must be nonnegative")
        week = await self.db.scalar(
            text("""
                SELECT date_trunc('week', statement_timestamp() AT TIME ZONE 'UTC')::date
                FROM agents WHERE id = :agent_id FOR NO KEY UPDATE
            """),
            {"agent_id": agent_id},
        )
        if week is None:
            return 0
        params = {"agent_id": agent_id, "week": week, "points": points, "commits": commits}
        await self.db.execute(
            text("""
                INSERT INTO agent_code_karma_limits (agent_id, week_start, awarded)
                VALUES (:agent_id, :week, 0) ON CONFLICT DO NOTHING
            """),
            params,
        )
        granted = await self.db.scalar(
            text("""
                WITH allowance AS (
                    SELECT LEAST(:points, 50 - awarded) AS granted
                    FROM agent_code_karma_limits
                    WHERE agent_id = :agent_id AND week_start = :week
                ), reservation AS (
                    UPDATE agent_code_karma_limits AS budget
                    SET awarded = budget.awarded + allowance.granted
                    FROM allowance
                    WHERE budget.agent_id = :agent_id AND budget.week_start = :week
                    RETURNING allowance.granted
                )
                UPDATE agents SET karma = karma + reservation.granted,
                    code_commits = code_commits + :commits
                FROM reservation WHERE id = :agent_id RETURNING reservation.granted
            """),
            params,
        )
        return int(granted)
