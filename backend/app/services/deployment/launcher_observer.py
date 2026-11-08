"""Recovery proofs from the launcher listing (design §6). Unknown is never absent."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from loguru import logger

from app.schemas.deployments import (
    DeploymentLease,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.launcher_client import LauncherClient

Proof = SandboxStopProof | SandboxNamespaceAbsenceProof


class LauncherObserver:
    """Replaces `_no_sandbox_observed`; a launcher failure yields no proof, so recovery retries."""

    def __init__(
        self, client: LauncherClient, status_of: Callable[[UUID], Awaitable[str | None]]
    ) -> None:
        self.client = client
        self.status_of = status_of

    async def __call__(self, lease: DeploymentLease, sandbox_id: str | None) -> Proof | None:
        ns = lease.sandbox_namespace
        try:
            listing = await self.client.list_runtimes()
            entry = next((r for r in listing if r.get("namespace") == ns), None)
            if sandbox_id is not None and await self.status_of(lease.deployment_id) == "active":
                return SandboxStopProof(lease=lease, sandbox_id=sandbox_id)
            if entry is None:
                await self.client.delete(ns)  # a bare network may remain
                if sandbox_id is None:
                    return SandboxNamespaceAbsenceProof(lease=lease, namespace=ns)
                return SandboxStopProof(lease=lease, sandbox_id=sandbox_id)
            await self.client.stop(ns)
        except DeployRejectedError as exc:
            logger.warning("recovery observation unknown ns={} reason={}", ns, exc)
            return None
        stopped_id = sandbox_id or entry.get("container_id")
        if not stopped_id:
            return None
        return SandboxStopProof(lease=lease, sandbox_id=stopped_id)
