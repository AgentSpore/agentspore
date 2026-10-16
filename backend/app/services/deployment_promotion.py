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
        for _ in range(3):
            try:
                async with asyncio.timeout(2):
                    status_code = await self.health(lease)
            except (TimeoutError, OSError, TransportError):
                status_code = 0
            async with self.sessions.begin() as db:
                repo = DeploymentRepository(db)
                accepted = await repo.record_health(lease, status_code)
                if await repo.promote(lease):
                    return True
                if not accepted:
                    return False
        return False
