"""Durable queue and global fencing in the caller's PostgreSQL transaction."""

import re
from uuid import UUID

from fastapi import Depends
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.deployments import (
    DeploymentEnqueue,
    DeploymentLease,
    DeploymentRecoverySnapshot,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)


class DeploymentConflictError(ValueError):
    """An immutable request or unfinished deployment conflicts with admission."""


class DeploymentRepository:
    """Lock the global lease before every ownership-sensitive state change."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def enqueue(self, request: DeploymentEnqueue) -> UUID:
        """Deduplicate under the project lock and preserve hostname reservations."""
        project = await self.db.scalar(
            text("SELECT id FROM projects WHERE id = :id FOR UPDATE"), {"id": request.project_id}
        )
        if project is None:
            raise DeploymentConflictError("project_missing")
        params = request.model_dump()
        params["commit_sha"] = request.commit_sha.lower()
        previous = (
            (
                await self.db.execute(
                    text(
                        "SELECT id, commit_sha, repository_url FROM project_deployments "
                        "WHERE project_id = :project_id AND idempotency_key = :idempotency_key"
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if previous is not None:
            if (previous["commit_sha"], previous["repository_url"]) != (
                params["commit_sha"],
                params["repository_url"],
            ):
                raise DeploymentConflictError("idempotency_conflict")
            return previous["id"]
        unfinished = await self.db.scalar(
            text(
                "SELECT id FROM project_deployments WHERE project_id = :project_id "
                "AND status IN ('queued', 'building', 'probing', 'promoting', 'recovering')"
            ),
            params,
        )
        if unfinished is not None:
            raise DeploymentConflictError("deployment_in_progress")
        await self.db.execute(
            text(
                "INSERT INTO service_hostnames (handle, original_project_id) "
                "VALUES (:handle, :project_id) ON CONFLICT DO NOTHING"
            ),
            {"handle": f"p-{request.project_id.hex}", "project_id": request.project_id},
        )
        reservation = await self.db.scalar(
            text("SELECT handle FROM service_hostnames WHERE original_project_id = :project_id"),
            params,
        )
        if reservation is None:
            raise DeploymentConflictError("hostname_conflict")
        inserted = await self.db.execute(
            text(
                "INSERT INTO project_deployments "
                "(project_id, idempotency_key, commit_sha, repository_url) "
                "VALUES (:project_id, :idempotency_key, :commit_sha, :repository_url) RETURNING id"
            ),
            params,
        )
        return inserted.scalar_one()

    async def _slot(self) -> RowMapping:
        return (
            (
                await self.db.execute(
                    text(
                        "SELECT *, expires_at <= clock_timestamp() AS expired "
                        "FROM deployment_worker_lease WHERE slot = 1 FOR UPDATE"
                    )
                )
            )
            .mappings()
            .one()
        )

    @staticmethod
    def _matches(slot: RowMapping, lease: DeploymentLease) -> bool:
        return all(
            slot[name] == getattr(lease, name)
            for name in ("deployment_id", "worker_id", "generation")
        )

    async def claim(self, worker_id: UUID, seconds: int = 30) -> DeploymentLease | None:
        """Persist owned scope; caller must commit before external creation.

        Expiry blocks replacement until owned stop is proven.
        """
        if not 1 <= seconds <= 300:
            raise ValueError("invalid_lease_duration")
        slot = await self._slot()
        if slot["deployment_id"] is not None:
            if slot["expired"]:
                await self.db.execute(
                    text(
                        "UPDATE project_deployments SET status = 'recovering' "
                        "WHERE id = :id AND status <> 'active'"
                    ),
                    {"id": slot["deployment_id"]},
                )
            return None
        job = await self.db.scalar(
            text(
                "SELECT d.id FROM project_deployments d JOIN projects p ON p.id = d.project_id "
                "WHERE d.status = 'queued' ORDER BY d.created_at, d.id LIMIT 1 "
                "FOR UPDATE OF d, p SKIP LOCKED"
            )
        )
        if job is None:
            return None
        lease = DeploymentLease(
            deployment_id=job, worker_id=worker_id, generation=slot["generation"] + 1
        )
        params = {
            **lease.model_dump(),
            "seconds": seconds,
            "sandbox_namespace": lease.sandbox_namespace,
        }
        await self.db.execute(
            text(
                "UPDATE deployment_worker_lease SET deployment_id = :deployment_id, "
                "worker_id = :worker_id, generation = :generation, "
                "expires_at = clock_timestamp() + CAST(:seconds AS integer) * interval '1 second' "
                "WHERE slot = 1"
            ),
            params,
        )
        await self.db.execute(
            text(
                "UPDATE project_deployments SET status = 'building', worker_id = :worker_id, "
                "generation = :generation, sandbox_namespace = :sandbox_namespace "
                "WHERE id = :deployment_id"
            ),
            params,
        )
        return lease

    async def record_sandbox(self, lease: DeploymentLease, sandbox_id: str) -> bool:
        """Persist the external sandbox identity once, only under a live fence."""
        if not sandbox_id:
            raise ValueError("sandbox_id_required")
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET sandbox_id = :sandbox_id "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND status = 'building' AND (sandbox_id IS NULL OR sandbox_id = :sandbox_id) "
                "AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot = 1 AND expires_at > clock_timestamp()) RETURNING id"
            ),
            {**lease.model_dump(), "sandbox_id": sandbox_id},
        )
        return updated is not None

    async def confirm_stopped(self, proof: SandboxStopProof) -> bool:
        """Release an owned build after trusted stop, preserving successful runtime state."""
        lease = proof.lease
        slot = await self._slot()
        if not self._matches(slot, lease):
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET "
                "status = CASE WHEN status = 'active' THEN status ELSE 'failed' END, "
                "reason_code = CASE WHEN status = 'active' THEN reason_code "
                "ELSE 'lease_expired' END "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND (status = 'active' OR (status = 'recovering' AND :expired)) "
                "AND sandbox_id = :sandbox_id RETURNING id"
            ),
            {**lease.model_dump(), "sandbox_id": proof.sandbox_id, "expired": slot["expired"]},
        )
        if updated is None:
            return False
        await self.db.execute(
            text(
                "UPDATE deployment_worker_lease SET deployment_id = NULL, worker_id = NULL, "
                "expires_at = NULL WHERE slot = 1"
            )
        )
        return True

    async def confirm_namespace_absent(self, proof: SandboxNamespaceAbsenceProof) -> bool:
        """Recover a pre-ID crash only after a trusted owned-namespace inventory."""
        lease = proof.lease
        if proof.namespace != lease.sandbox_namespace:
            return False
        slot = await self._slot()
        if not self._matches(slot, lease) or not slot["expired"]:
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET status = 'failed', reason_code = 'lease_expired' "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND status = 'recovering' AND sandbox_id IS NULL "
                "AND sandbox_namespace = :namespace RETURNING id"
            ),
            {**lease.model_dump(), "namespace": proof.namespace},
        )
        if updated is None:
            return False
        await self.db.execute(
            text(
                "UPDATE deployment_worker_lease SET deployment_id = NULL, worker_id = NULL, "
                "expires_at = NULL WHERE slot = 1"
            )
        )
        return True

    async def begin_probing(self, lease: DeploymentLease, image_digest: str) -> bool:
        """Bind a trusted immutable image to the canonical deployment revision."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET status = 'probing', image_digest = :digest, "
                "probe_started_at = COALESCE(probe_started_at, clock_timestamp()) "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND sandbox_id IS NOT NULL AND status IN ('building', 'probing') "
                "AND (image_digest IS NULL OR image_digest = :digest) "
                "AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot = 1 AND expires_at > clock_timestamp()) RETURNING id"
            ),
            {**lease.model_dump(), "digest": image_digest},
        )
        return updated is not None

    async def record_health(self, lease: DeploymentLease, status_code: int) -> bool:
        """Record a trusted observation; retain the build lease on every outcome."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        status = await self.db.scalar(
            text(
                "UPDATE project_deployments SET "
                "status = CASE WHEN :code <> 200 OR clock_timestamp() > "
                "probe_started_at + interval '60 seconds' THEN 'recovering' "
                "WHEN health_successes = 2 THEN 'promoting' ELSE 'probing' END, "
                "reason_code = CASE WHEN :code <> 200 THEN 'health_failed' "
                "WHEN clock_timestamp() > probe_started_at + interval '60 seconds' "
                "THEN 'probe_deadline' ELSE NULL END, "
                "health_successes = CASE WHEN :code = 200 AND clock_timestamp() <= "
                "probe_started_at + interval '60 seconds' THEN health_successes + 1 ELSE 0 END "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND status = 'probing' AND health_successes < 3 "
                "AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot = 1 AND expires_at > clock_timestamp()) RETURNING status"
            ),
            {**lease.model_dump(), "code": status_code},
        )
        return status in {"probing", "promoting"}

    async def promote(self, lease: DeploymentLease) -> bool:
        """Publish under slot then project locks; caller commits route and state together."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        job = (
            (
                await self.db.execute(
                    text(
                        "SELECT project_id, status FROM project_deployments "
                        "WHERE id = :deployment_id AND generation = :generation "
                        "AND worker_id = :worker_id AND image_digest IS NOT NULL"
                    ),
                    lease.model_dump(),
                )
            )
            .mappings()
            .first()
        )
        if job is None or job["status"] not in {"promoting", "active"}:
            return False
        project_id = job["project_id"]
        project = await self.db.scalar(
            text("SELECT id FROM projects WHERE id = :id FOR UPDATE"), {"id": project_id}
        )
        handle = await self.db.scalar(
            text("SELECT handle FROM service_hostnames WHERE original_project_id = :id"),
            {"id": project_id},
        )
        if project is None or handle is None:
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET status = 'active' "
                "WHERE id = :deployment_id AND status IN ('promoting', 'active') "
                "AND health_successes = 3 AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot = 1 AND expires_at > clock_timestamp()) RETURNING id"
            ),
            lease.model_dump(),
        )
        if updated is None:
            return False
        await self.db.execute(
            text(
                "INSERT INTO service_routes (project_id, handle, active_revision) "
                "VALUES (:project, :handle, :revision) ON CONFLICT (project_id) "
                "DO UPDATE SET active_revision = EXCLUDED.active_revision"
            ),
            {"project": project_id, "handle": handle, "revision": lease.deployment_id},
        )
        return True

    async def recovery_snapshot(self) -> DeploymentRecoverySnapshot | None:
        """Capture an active build or expired recovery without retaining locks over I/O."""
        slot = await self._slot()
        if slot["deployment_id"] is None:
            return None
        lease = DeploymentLease(
            **{name: slot[name] for name in ("deployment_id", "worker_id", "generation")}
        )
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT status,sandbox_id,sandbox_namespace FROM project_deployments "
                        "WHERE id=:deployment_id AND generation=:generation "
                        "AND worker_id=:worker_id"
                    ),
                    lease.model_dump(),
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["sandbox_namespace"] != lease.sandbox_namespace:
            return None
        if row["status"] != "active" and not (row["status"] == "recovering" and slot["expired"]):
            return None
        return DeploymentRecoverySnapshot(lease=lease, sandbox_id=row["sandbox_id"])

    async def confirm_observed_stopped(self, proof: SandboxStopProof) -> bool:
        """Adopt only a trusted pre-ID stop under the exact expired recovery fence."""
        if re.fullmatch(r"[0-9a-f]{64}", proof.sandbox_id) is None:
            return False
        lease = proof.lease
        slot = await self._slot()
        if not self._matches(slot, lease) or not slot["expired"]:
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET sandbox_id=:sandbox_id, status='failed', "
                "reason_code='lease_expired' WHERE id=:deployment_id AND generation=:generation "
                "AND worker_id=:worker_id AND status='recovering' AND sandbox_id IS NULL "
                "AND sandbox_namespace=:namespace "
                "AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot=1 AND expires_at <= clock_timestamp()) RETURNING id"
            ),
            {
                **lease.model_dump(),
                "sandbox_id": proof.sandbox_id,
                "namespace": lease.sandbox_namespace,
            },
        )
        if updated is None:
            return False
        await self.db.execute(
            text(
                "UPDATE deployment_worker_lease SET deployment_id=NULL, worker_id=NULL, "
                "expires_at=NULL WHERE slot=1"
            )
        )
        return True


def get_deployment_repo(db: AsyncSession = Depends(get_db)) -> DeploymentRepository:
    """Inject the request-owned transaction into the queue repository."""
    return DeploymentRepository(db)
