"""One worker step: recover, claim, fetch, manifest, pre-scan, build, record the outcome."""

import asyncio
import contextlib
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.deployment_repo import DeploymentRepository
from app.schemas.deployments import DeploymentLease, SandboxNamespaceAbsenceProof
from app.services.deployment.builder import Builder, BuildRequest
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.fetcher import fetch_source
from app.services.deployment.manifest import load_manifest
from app.services.deployment.scans import Scanner, run_prescan
from app.services.deployment_recovery import DeploymentRecoveryService

Fetch = Callable[[str, str, Path], Awaitable[None]]

SHUTDOWN_WRITE_TIMEOUT = 10.0


class LeaseLostError(Exception):
    """The fence expired or moved on while a build was running."""


async def _no_sandbox_observed(lease: DeploymentLease, sandbox_id: str | None):
    """Proof source while only `FakeBuilder` exists: nothing external can have been created.

    P3 replaces this with `DeploymentBuildCleanup.observe_stopped`.
    """
    if sandbox_id is not None:
        return None
    return SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)


class DeploymentWorker:
    """Single-slot worker; the global lease row in PostgreSQL is the only coordination."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        builder: Builder,
        scanners: list[Scanner],
        fetch: Fetch = fetch_source,
        lease_seconds: int = 60,
    ) -> None:
        self.sessions = sessions
        self.builder = builder
        self.scanners = scanners
        self.fetch = fetch
        self.lease_seconds = lease_seconds
        self.worker_id: UUID = uuid4()
        self.recovery = DeploymentRecoveryService(sessions, _no_sandbox_observed)

    async def run_once(self) -> bool:
        """Process one queued job, else recover an expired build; True when anything happened."""
        claimed = await self._claim()
        if claimed is None:
            return await self.recovery.recover_once()
        lease, repo_url, commit_sha = claimed
        try:
            reason = await self._supervised(lease, repo_url, commit_sha)
        except LeaseLostError:
            logger.warning("deploy lease lost deployment={}", lease.deployment_id)
            return True
        except asyncio.CancelledError:
            await self._record_on_shutdown(lease)
            raise
        await self._record(lease, reason)
        return True

    async def _record_on_shutdown(self, lease: DeploymentLease) -> None:
        """Best effort and bounded: a dead database must not keep the worker from exiting."""
        try:
            async with asyncio.timeout(SHUTDOWN_WRITE_TIMEOUT):
                await asyncio.shield(self._record(lease, "worker_shutdown"))
        except Exception as exc:  # noqa: BLE001 (shutdown path: the lease expires and recovery closes the row)
            logger.error(
                "deploy shutdown write failed deployment={} error={}",
                lease.deployment_id,
                type(exc).__name__,
            )

    async def _record(self, lease: DeploymentLease, reason: str) -> None:
        async with self.sessions.begin() as db:
            if not await DeploymentRepository(db).fail(lease, reason):
                logger.warning(
                    "deploy outcome dropped, fence gone deployment={}", lease.deployment_id
                )

    async def _claim(self) -> tuple[DeploymentLease, str, str] | None:
        async with self.sessions.begin() as db:
            repo = DeploymentRepository(db)
            lease = await repo.claim(self.worker_id, self.lease_seconds)
            if lease is None:
                return None
            return lease, *await repo.job_source(lease)

    async def _supervised(self, lease: DeploymentLease, repo_url: str, commit_sha: str) -> str:
        """Run the pipeline while renewing the lease; cancel it the moment the fence is lost."""
        job = asyncio.create_task(self._pipeline(lease, repo_url, commit_sha))
        try:
            while not job.done():
                await asyncio.wait({job}, timeout=self.lease_seconds / 3)
                if not job.done() and not await self._renew(lease):
                    raise LeaseLostError
            return job.result()
        finally:
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(job, return_exceptions=True)

    async def _renew(self, lease: DeploymentLease) -> bool:
        async with self.sessions.begin() as db:
            return await DeploymentRepository(db).renew(lease, self.lease_seconds)

    async def _pipeline(self, lease: DeploymentLease, repo_url: str, commit_sha: str) -> str:
        """Return the stable reason code; the temp dir never outlives the call."""
        try:
            with tempfile.TemporaryDirectory(prefix="a4-") as tmp:
                root = Path(tmp) / "src"
                await self.fetch(repo_url, commit_sha, root)
                load_manifest(root)
                await run_prescan(root, self.scanners)
                tag = f"{lease.sandbox_namespace}:{commit_sha[:12]}"
                result = await self.builder.build(BuildRequest(context_dir=root, tag=tag))
                if result.artifact is not None:  # until the push step consumes the tarball
                    result.artifact.unlink(missing_ok=True)
        except DeployRejectedError as exc:
            logger.warning("deploy refused deployment={} reason={}", lease.deployment_id, exc)
            return exc.reason_code
        except Exception:  # noqa: BLE001 (worker boundary: any step crash must end in a stored failed row, not a stuck lease)
            logger.exception("deploy step crashed deployment={}", lease.deployment_id)
            return "internal_error"
        return "runtime_unavailable"
