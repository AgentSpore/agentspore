"""Successful revisions survive exact owned build-sandbox cleanup."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from test_deployment_promotion import DIGEST
from test_deployment_promotion import deployment_engine as deployment_engine
from test_deployment_promotion import deployment_postgres as deployment_postgres
from test_deployment_promotion import deployment_project as deployment_project
from test_deployment_promotion import deployment_session as deployment_session
from test_deployment_promotion import promotion_world as promotion_world
from test_deployment_promotion import queue_world as queue_world

from app.repositories.deployment_repo import DeploymentRepository
from app.schemas.deployments import SandboxNamespaceAbsenceProof, SandboxStopProof

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
EXPIRE = text(
    "UPDATE deployment_worker_lease SET expires_at = clock_timestamp() - interval '1 second'"
)


@pytest_asyncio.fixture(loop_scope="module")
async def active_world(promotion_world):
    """Activate one trusted revision and queue the next without touching runtime."""
    maker, request, lease, _ = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
        assert await repo.promote(lease)
        next_job = await repo.enqueue(request.model_copy(update={"idempotency_key": uuid4()}))
    return maker, lease, next_job


async def test_expired_build_lease_does_not_reclassify_successful_revision(active_world):
    maker, lease, _ = active_world
    async with maker.begin() as db:
        await db.execute(EXPIRE)
    async with maker.begin() as db:
        assert await DeploymentRepository(db).claim(uuid4()) is None
        assert (
            await db.scalar(
                text("SELECT status FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
            == "active"
        )
        assert (
            await db.scalar(text("SELECT active_revision FROM service_routes"))
            == lease.deployment_id
        )
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease"))
            == lease.deployment_id
        )


@pytest.mark.parametrize("expired", [False, True])
async def test_exact_build_stop_releases_active_lease_without_losing_runtime(active_world, expired):
    maker, lease, next_job = active_world
    proof = SandboxStopProof(lease=lease, sandbox_id=f"a4-build-{lease.deployment_id.hex}")
    async with maker.begin() as db:
        if expired:
            await db.execute(EXPIRE)
            assert await DeploymentRepository(db).claim(uuid4()) is None
        assert await DeploymentRepository(db).confirm_stopped(proof)
    async with maker() as db:
        row = (
            await db.execute(
                text("SELECT status,image_digest,sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
        ).one()
        assert row.status == "active" and row.image_digest == DIGEST
        assert row.sandbox_id == proof.sandbox_id
        assert (
            await db.scalar(text("SELECT active_revision FROM service_routes"))
            == lease.deployment_id
        )
        assert await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease")) is None
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        fresh = await repo.claim(uuid4())
        assert fresh is not None and fresh.deployment_id == next_job
        assert fresh.generation > lease.generation
        assert not await repo.confirm_stopped(proof)
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease")) == next_job
        )


@pytest.mark.parametrize("wrong", ["build_id", "runtime_id", "worker", "generation", "namespace"])
async def test_unowned_or_namespace_only_proof_cannot_release_active_build_lease(
    active_world, wrong
):
    maker, lease, _ = active_world
    async with maker.begin() as db:
        await db.execute(EXPIRE)
        repo = DeploymentRepository(db)
        if wrong == "namespace":
            assert not await repo.confirm_namespace_absent(
                SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)
            )
        else:
            fence = lease
            sandbox = f"a4-build-{lease.deployment_id.hex}"
            if wrong == "build_id":
                sandbox = "a4-build-unowned"
            elif wrong == "runtime_id":
                sandbox = f"svc-{lease.deployment_id.hex}"
            elif wrong == "worker":
                fence = lease.model_copy(update={"worker_id": uuid4()})
            else:
                fence = lease.model_copy(update={"generation": lease.generation + 1})
            assert not await repo.confirm_stopped(SandboxStopProof(lease=fence, sandbox_id=sandbox))
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease"))
            == lease.deployment_id
        )
        assert (
            await db.scalar(
                text("SELECT status FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
            == "active"
        )
        assert (
            await db.scalar(text("SELECT active_revision FROM service_routes"))
            == lease.deployment_id
        )
