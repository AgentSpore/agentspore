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

    async def count_first_push(self, project_id) -> None:
        """Count the project's first push exactly once (first_pushed_at is the flag)."""
        await self._guarded(self._claim_push, project_id)

    async def count_event(self, step: str, source: str = "direct") -> None:
        """Count one event inside the caller's transaction, isolated by a SAVEPOINT."""
        await self._guarded(self._bump, step, source)

    async def _claim_push(self, project_id) -> None:
        claimed = await self.db.execute(
            text(
                "UPDATE projects SET first_pushed_at = NOW() "
                "WHERE id = :id AND first_pushed_at IS NULL RETURNING id"
            ),
            {"id": project_id},
        )
        if claimed.first() is not None:
            await self._bump("project_first_push", "direct")

    async def _bump(self, step: str, source: str) -> None:
        await self.increment(datetime.now(timezone.utc).date(), step, source)

    async def _guarded(self, action, *args) -> None:
        """Run a funnel write in a SAVEPOINT; its failure never poisons the caller's transaction."""
        try:
            async with self.db.begin_nested():
                await action(*args)
        except SQLAlchemyError as exc:
            logger.warning("funnel write failed: err={}", type(exc).__name__)

    async def rows_since(self, since: date) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT day, step, source, count FROM funnel_daily
                WHERE day >= :since ORDER BY day DESC, step, source
            """),
            {"since": since},
        )
        return [dict(r) for r in result.mappings()]
