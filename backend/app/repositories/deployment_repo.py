"""Durable queue and global fencing in the caller's PostgreSQL transaction."""

import re
from datetime import timedelta
from uuid import UUID

from fastapi import Depends
from loguru import logger
from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.deployment_quota import check_quotas
from app.schemas.deployments import (
    DeploymentEnqueue,
    DeploymentLease,
    DeploymentRecoverySnapshot,
    QuotaLimits,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)


class DeploymentConflictError(ValueError):
    """An immutable request or unfinished deployment conflicts with admission."""


class DeploymentRepository:
    """Lock the global lease before every ownership-sensitive state change."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def enqueue(
        self, request: DeploymentEnqueue, limits: QuotaLimits = QuotaLimits()
    ) -> UUID:
        """Deduplicate under the project lock and preserve hostname reservations."""
        project = (
            (
                await self.db.execute(
                    text(
                        "SELECT p.id, a.agent_type = 'internal' AS platform_service "
                        "FROM projects p JOIN agents a ON a.id = p.creator_agent_id "
                        "WHERE p.id = :id FOR UPDATE OF p"
                    ),
                    {"id": request.project_id},
                )
            )
            .mappings()
            .first()
        )
        if project is None:
            raise DeploymentConflictError("project_missing")
        # INVARIANT(A4): platform services are deployed by compose with a fixed alias
        # (V99__platform_services.sql). A pipeline deploy would overwrite active_revision
        # and orphan the container; if removed, promote() silently repoints the route.
        if project["platform_service"]:
            raise DeploymentConflictError("platform_service")
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
        await check_quotas(self.db, request.project_id, limits)
        reservation = await self._book_handle(request)
        if reservation is None:
            raise DeploymentConflictError("hostname_conflict")
        inserted = await self.db.execute(
            text(
                "INSERT INTO project_deployments (project_id, idempotency_key, commit_sha, "
                "repository_url, creator_agent_id, owner_user_id) "
                "SELECT p.id, :idempotency_key, :commit_sha, :repository_url, "
                "p.creator_agent_id, a.owner_user_id FROM projects p "
                "JOIN agents a ON a.id = p.creator_agent_id WHERE p.id = :project_id RETURNING id"
            ),
            params,
        )
        return inserted.scalar_one()

    async def _book_handle(self, request: DeploymentEnqueue) -> str | None:
        """Permanent booking: the project slug if free, else `p-<project hex>`."""
        for handle in (request.handle_candidate, f"p-{request.project_id.hex}"):
            if handle is None:
                continue
            await self.db.execute(
                text(
                    "INSERT INTO service_hostnames (handle, original_project_id) "
                    "VALUES (:handle, :project_id) ON CONFLICT DO NOTHING"
                ),
                {"handle": handle, "project_id": request.project_id},
            )
            reservation = await self.db.scalar(
                text("SELECT handle FROM service_hostnames WHERE original_project_id = :id"),
                {"id": request.project_id},
            )
            if reservation is not None:
                return reservation
        return None

    async def deployment_status(self, deployment_id: UUID) -> str:
        """Stored status of one deployment."""
        return await self.db.scalar(
            text("SELECT status FROM project_deployments WHERE id = :id"), {"id": deployment_id}
        )

    async def status_view(
        self, project_id: UUID
    ) -> tuple[UUID | None, str | None, list[RowMapping]]:
        """Active revision, its hostname and the last five deployments."""
        route = (
            (
                await self.db.execute(
                    text(
                        "SELECT active_revision, handle FROM service_routes WHERE project_id = :id"
                    ),
                    {"id": project_id},
                )
            )
            .mappings()
            .first()
        )
        rows = (
            (
                await self.db.execute(
                    text(
                        "SELECT id, status, reason_code, commit_sha, created_at "
                        "FROM project_deployments WHERE project_id = :id "
                        "ORDER BY created_at DESC, id DESC LIMIT 5"
                    ),
                    {"id": project_id},
                )
            )
            .mappings()
            .all()
        )
        if route is None:
            return None, None, list(rows)
        return route["active_revision"], route["handle"], list(rows)

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

    async def job_source(self, lease: DeploymentLease) -> tuple[str, str]:
        """Repository URL and commit of the deployment this lease just claimed."""
        row = (
            await self.db.execute(
                text(
                    "SELECT repository_url, commit_sha FROM project_deployments "
                    "WHERE id = :deployment_id AND generation = :generation "
                    "AND worker_id = :worker_id AND status = 'building'"
                ),
                lease.model_dump(),
            )
        ).one()
        return row.repository_url, row.commit_sha

    async def renew(self, lease: DeploymentLease, seconds: int) -> bool:
        """Extend a live lease; an expired or replaced one stays untouched."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        await self.db.execute(
            text(
                "UPDATE deployment_worker_lease SET expires_at = clock_timestamp() "
                "+ CAST(:seconds AS integer) * interval '1 second' WHERE slot = 1"
            ),
            {"seconds": seconds},
        )
        return True

    async def fail(self, lease: DeploymentLease, reason_code: str) -> bool:
        """End a build that never created a sandbox and release the slot, under a live fence."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        updated = await self.db.scalar(
            text(
                "UPDATE project_deployments SET status = 'failed', reason_code = :reason_code "
                "WHERE id = :deployment_id AND generation = :generation "
                "AND worker_id = :worker_id AND status = 'building' AND sandbox_id IS NULL "
                "RETURNING id"
            ),
            {**lease.model_dump(), "reason_code": reason_code},
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
                "ELSE COALESCE(reason_code, 'lease_expired') END "
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

    async def probe_remaining(self, lease: DeploymentLease) -> float | None:
        """Read the original probe budget only while the caller owns the live fence."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return None
        remaining = await self.db.scalar(
            text(
                "SELECT EXTRACT(EPOCH FROM probe_started_at + interval '60 seconds' "
                "- clock_timestamp()) FROM project_deployments "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND status = 'probing'"
            ),
            lease.model_dump(),
        )
        return float(remaining) if remaining is not None else None

    async def record_health(
        self, lease: DeploymentLease, status_code: int, *, startup_retry: bool = False
    ) -> bool:
        """Record a trusted observation; retain the build lease on every outcome."""
        slot = await self._slot()
        if not self._matches(slot, lease) or slot["expired"]:
            return False
        status = await self.db.scalar(
            text(
                "UPDATE project_deployments SET "
                "status = CASE WHEN (:code <> 200 AND "
                "(NOT :startup_retry OR :code <> 0 OR health_successes > 0)) "
                "OR clock_timestamp() > "
                "probe_started_at + interval '60 seconds' THEN 'recovering' "
                "WHEN health_successes = 2 THEN 'promoting' ELSE 'probing' END, "
                "reason_code = CASE WHEN clock_timestamp() > "
                "probe_started_at + interval '60 seconds' "
                "THEN 'probe_deadline' WHEN :code <> 200 AND "
                "(NOT :startup_retry OR :code <> 0 OR health_successes > 0) "
                "THEN 'health_failed' ELSE NULL END, "
                "health_successes = CASE WHEN :code = 200 AND clock_timestamp() <= "
                "probe_started_at + interval '60 seconds' THEN health_successes + 1 ELSE 0 END "
                "WHERE id = :deployment_id AND generation = :generation AND worker_id = :worker_id "
                "AND status = 'probing' AND health_successes < 3 "
                "AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot = 1 AND expires_at > clock_timestamp()) RETURNING status"
            ),
            {**lease.model_dump(), "code": status_code, "startup_retry": startup_retry},
        )
        return status in {"probing", "promoting"}

    async def _activate_row(self, lease: DeploymentLease, project_id: UUID) -> bool:
        """Supersede the project's previous active row, then activate this one."""
        eligible = await self.db.scalar(
            text(
                "SELECT id FROM project_deployments "
                "WHERE id = :deployment_id AND status IN ('promoting', 'active') "
                "AND health_successes = 3 AND EXISTS (SELECT 1 FROM deployment_worker_lease "
                "WHERE slot = 1 AND expires_at > clock_timestamp()) FOR UPDATE"
            ),
            lease.model_dump(),
        )
        if eligible is None:
            return False
        await self.db.execute(
            text(
                "UPDATE project_deployments SET status = 'superseded' "
                "WHERE project_id = :project AND status = 'active' AND id <> :deployment_id"
            ),
            {"project": project_id, "deployment_id": lease.deployment_id},
        )
        await self.db.execute(
            text(
                "UPDATE project_deployments SET status = 'active', "
                "activated_at = COALESCE(activated_at, clock_timestamp()) "
                "WHERE id = :deployment_id"
            ),
            {"deployment_id": lease.deployment_id},
        )
        return True

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
        if not await self._activate_row(lease, project_id):
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

    async def retire_candidates(self, grace: timedelta = timedelta(seconds=30)) -> list[RowMapping]:
        """Superseded revisions whose successor has been active over `grace` (database clock)."""
        result = await self.db.execute(
            text(
                "SELECT d.id, d.sandbox_id, d.sandbox_namespace FROM project_deployments d "
                "WHERE d.status = 'superseded' AND d.retired_at IS NULL AND "
                "(SELECT min(n.activated_at) FROM project_deployments n "
                "WHERE n.project_id = d.project_id AND n.activated_at > d.activated_at) "
                "+ make_interval(secs => CAST(:grace AS double precision)) "
                "< clock_timestamp() ORDER BY d.id"
            ),
            {"grace": grace.total_seconds()},
        )
        return list(result.mappings().all())

    async def mark_retired(self, deployment_id: UUID) -> bool:
        """Record that a superseded revision's sandbox is gone; false if already retired."""
        retired = await self.db.scalar(
            text(
                "UPDATE project_deployments SET retired_at = clock_timestamp() "
                "WHERE id = :id AND status = 'superseded' AND retired_at IS NULL RETURNING id"
            ),
            {"id": deployment_id},
        )
        if retired is None:
            logger.warning("deployment {} was not retirable", deployment_id)
        return retired is not None

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
