"""Apply actual build-stop observations in fresh, independently fenced transactions."""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.deployment_repo import DeploymentRepository
from app.schemas.deployments import (
    DeploymentLease,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)


class DeploymentRecoveryService:
    """Close the ownership snapshot transaction before contacting the external observer."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        observer: Callable[
            [DeploymentLease, str | None],
            Awaitable[SandboxStopProof | SandboxNamespaceAbsenceProof | None],
        ],
    ) -> None:
        self.sessions = sessions
        self.observer = observer

    async def recover_once(self) -> bool:
        """Observe externally, then recheck the exact original ownership before cleanup."""
        async with self.sessions.begin() as db:
            snapshot = await DeploymentRepository(db).recovery_snapshot()
        if snapshot is None:
            return False
        proof = await self.observer(snapshot.lease, snapshot.sandbox_id)
        if proof is None or proof.lease != snapshot.lease:
            return False
        async with self.sessions.begin() as db:
            repository = DeploymentRepository(db)
            if isinstance(proof, SandboxNamespaceAbsenceProof):
                return await repository.confirm_namespace_absent(proof)
            if snapshot.sandbox_id is None:
                return await repository.confirm_observed_stopped(proof)
            return await repository.confirm_stopped(proof)
