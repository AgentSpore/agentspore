"""Retire idle runtimes while the PostgreSQL lease excludes new image pushes."""

import asyncio

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.repositories.deployment_cleanup_repo import DeploymentCleanupRepository
from app.repositories.deployment_repo import DeploymentRepository
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.launcher_client import LauncherClient


class RuntimeCleanup:
    """Keep the idle lease locked until destructive launcher calls have settled."""

    deadline_seconds = 120
    max_deletes = 20

    def __init__(self, sessions: async_sessionmaker[AsyncSession], client: LauncherClient):
        self.sessions = sessions
        self.client = client

    async def run_once(self) -> bool:
        """Sweep only an idle slot; an unknown launcher observation never proves absence."""
        if not get_settings().a4_deploy_enabled:
            return False
        try:
            async with asyncio.timeout(self.deadline_seconds), self.sessions.begin() as db:
                repo = DeploymentRepository(db)
                cleanup = DeploymentCleanupRepository(db)
                if not await cleanup.lock_idle_cleanup():
                    return False
                inventory = await self.client.list_namespaces()
                candidates = await repo.retire_candidates()
                rows = await cleanup.runtime_cleanup_rows()
                routed = {row["sandbox_namespace"] for row in rows if row["routed"]}
                retiring = {row["sandbox_namespace"]: row for row in candidates}
                protected = routed | {
                    row["sandbox_namespace"]
                    for row in rows
                    if row["status"] == "active"
                    or (
                        row["status"] == "superseded"
                        and row["retired_at"] is None
                        and row["sandbox_namespace"] not in retiring
                    )
                }
                targets = (inventory | set(retiring)) - protected - {None}
                changed = False
                for ns in sorted(targets)[: self.max_deletes]:
                    await self.client.delete(ns)
                    changed = True
                    if ns in retiring and not await repo.mark_retired(retiring[ns]["id"]):
                        logger.warning("runtime retirement was not recorded ns={}", ns)
                await self.client.gc_images(await cleanup.runtime_image_keep())
                return changed
        except (DeployRejectedError, TimeoutError) as exc:
            logger.warning("runtime cleanup incomplete error={}", type(exc).__name__)
            return False
