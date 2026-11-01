"""Trusted fixture health orchestration; no build, runtime creation or public API."""

import asyncio
from collections.abc import Awaitable, Callable

from httpx import TransportError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.deployment_repo import DeploymentRepository
from app.schemas.deployments import DeploymentLease


class DeploymentPromotionService:
    """Keep every health observation outside a short, independently fenced transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        health: Callable[[DeploymentLease], Awaitable[int]],
    ) -> None:
        self.sessions = sessions
        self.health = health

    async def probe_and_promote(self, lease: DeploymentLease) -> bool:
        """Require three sequential checks; persistent PostgreSQL time bounds the window."""
        async with self.sessions.begin() as db:
            if await DeploymentRepository(db).promote(lease):
                return True
        delay = 0.2
        while True:
            async with self.sessions.begin() as db:
                repo = DeploymentRepository(db)
                remaining = await repo.probe_remaining(lease)
                if remaining is None:
                    return False
                if remaining <= 0:
                    await repo.record_health(lease, 0)
                    return False
            deadline = asyncio.get_running_loop().time() + remaining
            try:
                async with asyncio.timeout(min(2, remaining)):
                    status_code = await self.health(lease)
            except (TimeoutError, OSError, TransportError):
                status_code = 0
            async with self.sessions.begin() as db:
                repo = DeploymentRepository(db)
                accepted = await repo.record_health(lease, status_code, startup_retry=True)
                if await repo.promote(lease):
                    return True
                if not accepted:
                    return False
            if status_code == 0:
                await asyncio.sleep(
                    min(delay, max(0, deadline - asyncio.get_running_loop().time()))
                )
                delay = min(delay * 2, 1)
