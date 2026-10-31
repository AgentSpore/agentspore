"""SQL for the aggregate funnel counters."""

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


class FunnelRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def increment(self, day: date, step: str, source: str) -> None:
        await self.db.execute(
            text("""
                INSERT INTO funnel_daily (day, step, source, count)
                VALUES (:day, :step, :source, 1)
                ON CONFLICT (day, step, source) DO UPDATE SET count = funnel_daily.count + 1
            """),
            {"day": day, "step": step, "source": source},
        )

    async def count_first_heartbeat(self, agent_id) -> None:
        """Count the agent's first heartbeat; call before last_heartbeat is stamped."""
        await self._count_once(
            "SELECT id FROM agents WHERE id = :id AND last_heartbeat IS NULL FOR UPDATE",
            agent_id,
            "agent_first_heartbeat",
        )

    async def count_first_push(self, project_id) -> None:
        """Count the project's first push exactly once (first_pushed_at is the flag)."""
        await self._count_once(
            "UPDATE projects SET first_pushed_at = NOW() "
            "WHERE id = :id AND first_pushed_at IS NULL RETURNING id",
            project_id,
            "project_first_push",
        )

    async def _count_once(self, claim_sql: str, row_id, step: str) -> None:
        """Run the claim statement and bump the step if it matched a row.

        A SAVEPOINT keeps a funnel failure from poisoning the caller's transaction.
        """
        try:
            async with self.db.begin_nested():
                claimed = (await self.db.execute(text(claim_sql), {"id": row_id})).first()
                if claimed is not None:
                    await self.increment(datetime.now(timezone.utc).date(), step, "direct")
        except SQLAlchemyError as exc:
            logger.warning("funnel claim failed: step={} err={}", step, type(exc).__name__)

    async def rows_since(self, since: date) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT day, step, source, count FROM funnel_daily
                WHERE day >= :since ORDER BY day DESC, step, source
            """),
            {"since": since},
        )
        return [dict(r) for r in result.mappings()]
