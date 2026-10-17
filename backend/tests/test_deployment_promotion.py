"""Fenced promotion of trusted fixtures; no builds or sandbox execution."""

import importlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from test_deployment_queue import deployment_engine as deployment_engine
from test_deployment_queue import deployment_postgres as deployment_postgres
from test_deployment_queue import deployment_project as deployment_project
from test_deployment_queue import deployment_session as deployment_session
from test_deployment_queue import queue_world as queue_world

from app.repositories.deployment_repo import DeploymentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
DIGEST = "sha256:" + "b" * 64


@pytest_asyncio.fixture(loop_scope="module")
async def promotion_world(queue_world):
    """Keep a real old registry target while the trusted candidate is prepared."""
    maker, (request, _) = queue_world
    previous = uuid4()
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE service_routes"))
        repo = DeploymentRepository(db)
        job = await repo.enqueue(request)
        lease = await repo.claim(uuid4())
        assert lease is not None and lease.deployment_id == job
        assert await repo.record_sandbox(lease, f"a4-build-{job.hex}")
        await db.execute(
            text(
                "INSERT INTO service_routes (project_id, handle, active_revision) "
                "SELECT :project, handle, :previous FROM service_hostnames "
                "WHERE original_project_id = :project"
            ),
            {"project": request.project_id, "previous": previous},
        )
    return maker, request, lease, previous


async def test_third_success_allows_atomic_idempotent_promotion(promotion_world):
    maker, request, lease, previous = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        assert not await repo.promote(lease)
        for _ in range(2):
            assert await repo.record_health(lease, 200)
            assert not await repo.promote(lease)
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous
        assert await repo.record_health(lease, 200)
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "promoting"
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.promote(lease)
        assert await repo.promote(lease)
    async with maker() as db:
        route = await db.scalar(
            text("SELECT active_revision FROM service_routes WHERE project_id = :project"),
            {"project": request.project_id},
        )
        assert route == lease.deployment_id
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "active"
        assert await db.scalar(text("SELECT image_digest FROM project_deployments")) == DIGEST
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease"))
            == lease.deployment_id
        )


@pytest.mark.parametrize("status_code", [301, 500, 0])
async def test_failed_health_preserves_old_revision_and_owned_cleanup(promotion_world, status_code):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        assert await repo.record_health(lease, 200)
        assert not await repo.record_health(lease, status_code)
        assert not await repo.promote(lease)
    async with maker() as db:
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "recovering"
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease"))
            == lease.deployment_id
        )
        assert (
            await db.scalar(text("SELECT sandbox_id FROM project_deployments"))
            == f"a4-build-{lease.deployment_id.hex}"
        )


async def test_restart_before_promotion_keeps_previous_route(promotion_world):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
    async with maker() as db:
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "promoting"
    async with maker.begin() as db:
        assert await DeploymentRepository(db).promote(lease)
    async with maker() as db:
        assert (
            await db.scalar(text("SELECT active_revision FROM service_routes"))
            == lease.deployment_id
        )


async def test_expired_and_wrong_fences_cannot_publish(promotion_world):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        wrong = lease.model_copy(update={"generation": lease.generation + 1})
        assert not await repo.begin_probing(wrong, DIGEST)
        assert await repo.begin_probing(lease, DIGEST)
        assert not await repo.record_health(wrong, 200)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
        assert not await repo.promote(wrong)
        await db.execute(
            text(
                "UPDATE deployment_worker_lease "
                "SET expires_at = clock_timestamp() - interval '1 second'"
            )
        )
        assert not await repo.promote(lease)
    async with maker() as db:
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous


async def test_probe_deadline_blocks_late_third_success(promotion_world):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        assert await repo.record_health(lease, 200)
        assert await repo.record_health(lease, 200)
        await db.execute(
            text(
                "UPDATE project_deployments "
                "SET probe_started_at = clock_timestamp() - interval '61 seconds'"
            )
        )
        assert not await repo.record_health(lease, 200)
        assert not await repo.promote(lease)
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous


async def test_route_and_active_status_rollback_together(promotion_world):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
    with pytest.raises(RuntimeError, match="synthetic_crash"):
        async with maker.begin() as db:
            assert await DeploymentRepository(db).promote(lease)
            raise RuntimeError("synthetic_crash")
    async with maker() as db:
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "promoting"
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease"))
            == lease.deployment_id
        )


async def test_health_boundary_runs_three_checks_then_publishes(promotion_world):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        assert await DeploymentRepository(db).begin_probing(lease, DIGEST)
    health = AsyncMock(return_value=200)
    module = importlib.import_module("app.services.deployment_promotion")
    service = module.DeploymentPromotionService(maker, health)
    assert await service.probe_and_promote(lease)
    assert health.await_count == 3
    assert all(call.args == (lease,) for call in health.await_args_list)
    async with maker() as db:
        assert (
            await db.scalar(text("SELECT active_revision FROM service_routes"))
            == lease.deployment_id
        )
        assert previous != lease.deployment_id


async def test_deleted_project_cannot_be_resurrected_by_promotion(promotion_world):
    maker, request, lease, _ = promotion_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.begin_probing(lease, DIGEST)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
        await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": request.project_id})
    async with maker.begin() as db:
        assert not await DeploymentRepository(db).promote(lease)
        assert await db.scalar(text("SELECT count(*) FROM service_routes")) == 0
        assert (
            await db.scalar(text("SELECT project_id FROM project_deployments"))
            == request.project_id
        )
        assert (
            await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease"))
            == lease.deployment_id
        )


@pytest.mark.parametrize("response", [500, TimeoutError("synthetic_timeout")])
async def test_health_boundary_failure_never_publishes(promotion_world, response):
    maker, _, lease, previous = promotion_world
    async with maker.begin() as db:
        assert await DeploymentRepository(db).begin_probing(lease, DIGEST)
    effects = [200, response]
    health = AsyncMock(side_effect=effects)
    module = importlib.import_module("app.services.deployment_promotion")
    service = module.DeploymentPromotionService(maker, health)
    assert not await service.probe_and_promote(lease)
    assert health.await_count == 2
    async with maker() as db:
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "recovering"
