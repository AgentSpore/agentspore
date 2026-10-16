"""Recovery joins actual PostgreSQL fences with only outward observer substitution."""

import asyncio
import importlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from test_deployment_active_cleanup import active_world as active_world
from test_deployment_active_cleanup import deployment_engine as deployment_engine
from test_deployment_active_cleanup import deployment_postgres as deployment_postgres
from test_deployment_active_cleanup import deployment_project as deployment_project
from test_deployment_active_cleanup import deployment_session as deployment_session
from test_deployment_active_cleanup import promotion_world as promotion_world
from test_deployment_active_cleanup import queue_world as queue_world

from app.repositories.deployment_repo import DeploymentRepository
from app.schemas.deployments import SandboxNamespaceAbsenceProof, SandboxStopProof

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
OBSERVED = "c" * 64
SLOT = text("SELECT deployment_id FROM deployment_worker_lease")


@pytest_asyncio.fixture(loop_scope="module")
async def recovery_world(queue_world):
    maker, (request, second) = queue_world
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE service_routes"))
        repo = DeploymentRepository(db)
        job = await repo.enqueue(request)
        next_job = await repo.enqueue(second)
        lease = await repo.claim(uuid4())
        assert lease is not None and lease.deployment_id == job
        await db.execute(
            text(
                "UPDATE deployment_worker_lease "
                "SET expires_at = clock_timestamp() - interval '1 second'"
            )
        )
    async with maker.begin() as db:
        assert await DeploymentRepository(db).claim(uuid4()) is None
    return maker, lease, next_job


@pytest.fixture
def recovery_service():
    return importlib.import_module("app.services.deployment_recovery").DeploymentRecoveryService


async def test_pre_id_observed_stop_adopts_identity_and_releases_fenced_queue(
    recovery_world, recovery_service
):
    maker, lease, next_job = recovery_world
    proof = SandboxStopProof(lease=lease, sandbox_id=OBSERVED)
    async with maker.begin() as db:
        assert not await DeploymentRepository(db).confirm_stopped(proof)
    observer = AsyncMock(return_value=proof)
    assert await recovery_service(maker, observer).recover_once()
    observer.assert_awaited_once_with(lease, None)
    async with maker() as db:
        row = (
            await db.execute(
                text("SELECT status,sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
        ).one()
        assert row.status == "failed" and row.sandbox_id == OBSERVED
        assert await db.scalar(SLOT) is None
        assert await db.scalar(text("SELECT count(*) FROM service_routes")) == 0
    async with maker.begin() as db:
        fresh = await DeploymentRepository(db).claim(uuid4())
        assert fresh is not None and fresh.deployment_id == next_job
        assert fresh.generation > lease.generation


async def test_recorded_active_build_cleanup_keeps_successful_runtime(
    active_world, recovery_service
):
    maker, lease, _ = active_world
    identifier = f"a4-build-{lease.deployment_id.hex}"
    observer = AsyncMock(return_value=SandboxStopProof(lease=lease, sandbox_id=identifier))
    assert await recovery_service(maker, observer).recover_once()
    observer.assert_awaited_once_with(lease, identifier)
    async with maker() as db:
        assert await db.scalar(SLOT) is None
        assert (
            await db.scalar(text("SELECT active_revision FROM service_routes"))
            == lease.deployment_id
        )
        assert (
            await db.scalar(
                text("SELECT status FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
            == "active"
        )


async def test_empty_namespace_observation_releases_only_pre_id_recovery(
    recovery_world, recovery_service
):
    maker, lease, _ = recovery_world
    observer = AsyncMock(
        return_value=SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)
    )
    assert await recovery_service(maker, observer).recover_once()
    async with maker() as db:
        assert await db.scalar(SLOT) is None
        assert (
            await db.scalar(
                text("SELECT sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
            is None
        )


@pytest.mark.parametrize("wrong_generation", [False, True])
async def test_unknown_daemon_or_stale_proof_keeps_original_lease(
    recovery_world, recovery_service, wrong_generation
):
    maker, lease, _ = recovery_world
    proof = (
        SandboxStopProof(
            lease=lease.model_copy(update={"generation": lease.generation + 1}), sandbox_id=OBSERVED
        )
        if wrong_generation
        else None
    )
    observer = AsyncMock(return_value=proof)
    assert not await recovery_service(maker, observer).recover_once()
    async with maker() as db:
        assert await db.scalar(SLOT) == lease.deployment_id
        assert (
            await db.scalar(
                text("SELECT sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
            is None
        )


@pytest.mark.parametrize("competing_id", [None, "d" * 64])
async def test_observer_holds_no_database_lock_and_competing_identity_is_preserved(
    recovery_world, recovery_service, competing_id
):
    maker, lease, _ = recovery_world
    started, release = asyncio.Event(), asyncio.Event()

    async def observe(fence, identifier):
        started.set()
        await release.wait()
        return SandboxStopProof(lease=fence, sandbox_id=OBSERVED)

    async with asyncio.TaskGroup() as group:
        task = group.create_task(recovery_service(maker, observe).recover_once())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            async with asyncio.timeout(1), maker.begin() as db:
                await db.execute(
                    text("UPDATE deployment_worker_lease SET expires_at=expires_at WHERE slot=1")
                )
                if competing_id is not None:
                    await db.execute(
                        text("UPDATE project_deployments SET sandbox_id=:id WHERE id=:job"),
                        {"id": competing_id, "job": lease.deployment_id},
                    )
        finally:
            release.set()
    assert task.result() is (competing_id is None)
    async with maker() as db:
        assert await db.scalar(
            text("SELECT sandbox_id FROM project_deployments WHERE id=:id"),
            {"id": lease.deployment_id},
        ) == (competing_id or OBSERVED)
        assert await db.scalar(SLOT) == (lease.deployment_id if competing_id else None)


async def test_replaced_global_fence_rejects_late_observation(recovery_world, recovery_service):
    maker, lease, next_job = recovery_world

    async def observe(fence, identifier):
        async with maker.begin() as db:
            repo = DeploymentRepository(db)
            assert await repo.confirm_namespace_absent(
                SandboxNamespaceAbsenceProof(lease=fence, namespace=fence.sandbox_namespace)
            )
            fresh = await repo.claim(uuid4())
            assert fresh is not None and fresh.deployment_id == next_job
        return SandboxStopProof(lease=fence, sandbox_id=OBSERVED)

    assert not await recovery_service(maker, observe).recover_once()
    async with maker() as db:
        assert await db.scalar(SLOT) == next_job
        assert (
            await db.scalar(
                text("SELECT sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
            is None
        )
