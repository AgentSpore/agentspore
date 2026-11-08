"""Worker steps after the push (design §5): create the runtime, start it, probe, promote."""

from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.deployment_repo import DeploymentRepository
from app.schemas.deployments import DeploymentLease
from app.services.deployment.errors import DeployRejectedError, LeaseLostError
from app.services.deployment.launcher_client import LauncherClient
from app.services.deployment_promotion import DeploymentPromotionService


class LauncherRuntime:
    """Q17: a failed launch is failed and cleaned up, never resumed."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: LauncherClient,
        promotion: DeploymentPromotionService,
        public_url: str,
    ) -> None:
        self.sessions = sessions
        self.client = client
        self.promotion = promotion
        self.public_url = public_url

    async def launch(self, lease: DeploymentLease, image_ref: str) -> None:
        """Return when the row is settled; raise `DeployRejectedError` while it can still fail.

        Failures before the sandbox id is stored propagate so the worker records the reason.
        Later ones hand the row to recovery (`recovering`), which closes it as failed.
        """
        ns = lease.sandbox_namespace
        try:
            container_id = await self.client.put(ns, self._request(lease, image_ref))
        except DeployRejectedError:
            await self._cleanup(ns)
            raise
        if not await self._record(lease, container_id):
            await self._cleanup(ns)
            raise LeaseLostError
        digest = image_ref.rpartition("@")[2]
        try:
            await self.client.start(ns)
        except DeployRejectedError as exc:
            logger.warning("deploy start refused deployment={} reason={}", lease.deployment_id, exc)
            await self._cleanup(ns)
            await self._hand_to_recovery(lease, digest)
            return
        if not await self._begin_probing(lease, digest):
            await self._cleanup(ns)
            raise LeaseLostError
        await self.promotion.probe_and_promote(lease)

    async def status_of(self, deployment_id: UUID) -> str | None:
        async with self.sessions() as db:
            return await DeploymentRepository(db).deployment_status(deployment_id)

    def _request(self, lease: DeploymentLease, image_ref: str) -> dict:
        return {
            "deployment_id": lease.deployment_id.hex,
            "generation": lease.generation,
            "worker_id": str(lease.worker_id),
            "image": image_ref,
            "env": {},
            "public_url": self.public_url,
        }

    async def _cleanup(self, ns: str) -> None:
        try:
            await self.client.delete(ns)
        except DeployRejectedError as exc:
            logger.error("deploy runtime cleanup failed ns={} reason={}", ns, exc)

    async def _record(self, lease: DeploymentLease, container_id: str) -> bool:
        async with self.sessions.begin() as db:
            return await DeploymentRepository(db).record_sandbox(lease, container_id)

    async def _begin_probing(self, lease: DeploymentLease, digest: str) -> bool:
        async with self.sessions.begin() as db:
            return await DeploymentRepository(db).begin_probing(lease, digest)

    async def _hand_to_recovery(self, lease: DeploymentLease, digest: str) -> None:
        """`recovering` is only reachable through a failed observation of a probing row."""
        async with self.sessions.begin() as db:
            repo = DeploymentRepository(db)
            if await repo.begin_probing(lease, digest):
                await repo.record_health(lease, 0)
