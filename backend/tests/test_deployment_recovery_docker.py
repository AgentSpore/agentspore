"""Real owned Docker stop observations persisted by real PostgreSQL recovery."""

import asyncio
import os
from uuid import uuid4

import docker
import pytest
import pytest_asyncio
from sqlalchemy import text
from test_deployment_promotion import DIGEST
from test_deployment_recovery import deployment_engine as deployment_engine
from test_deployment_recovery import deployment_postgres as deployment_postgres
from test_deployment_recovery import deployment_project as deployment_project
from test_deployment_recovery import deployment_session as deployment_session
from test_deployment_recovery import promotion_world as promotion_world
from test_deployment_recovery import queue_world as queue_world
from test_deployment_recovery import recovery_world as recovery_world

from app.repositories.deployment_repo import DeploymentRepository
from app.services.deployment_build_cleanup import DeploymentBuildCleanup
from app.services.deployment_recovery import DeploymentRecoveryService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
IMAGE = "sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"


@pytest_asyncio.fixture(loop_scope="module")
async def owned_build():
    """Use only the guarded child's synthetic Docker endpoint and ownership label."""
    client = await asyncio.to_thread(
        docker.APIClient, base_url=os.environ["DOCKER_HOST"], version="auto", timeout=3
    )

    async def create(lease):
        labels = {
            "af35.owner": os.environ["AF35_RUN_ID"],
            "com.agentspore.a4.namespace": lease.sandbox_namespace,
            "com.agentspore.a4.deployment": str(lease.deployment_id),
            "com.agentspore.a4.worker": str(lease.worker_id),
            "com.agentspore.a4.generation": str(lease.generation),
            "com.agentspore.a4.role": "build",
        }
        limits = client.create_host_config(
            mem_limit="16m",
            memswap_limit="16m",
            nano_cpus=100000000,
            pids_limit=16,
            read_only=True,
            network_mode="none",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            restart_policy={"Name": "no"},
        )
        created = await asyncio.to_thread(
            client.create_container,
            image=IMAGE,
            command=["sleep", "120"],
            user="65532",
            labels=labels,
            host_config=limits,
        )
        identifier = created["Id"]
        # The existing diagnostic plugin records this full ID before any startup.
        await asyncio.to_thread(client.start, identifier)
        rows = await asyncio.to_thread(
            client.containers, all=True, trunc=False, filters={"id": [identifier]}
        )
        assert len(rows) == 1 and rows[0]["Id"] == identifier
        assert all(rows[0]["Labels"].get(key) == value for key, value in labels.items())
        assert rows[0]["State"] == "running"
        return identifier

    try:
        yield create
    finally:
        await asyncio.to_thread(client.close)


async def test_real_pre_id_stop_adopts_full_id_and_unlocks_queue(recovery_world, owned_build):
    maker, lease, next_job = recovery_world
    identifier = await owned_build(lease)
    observer = DeploymentBuildCleanup(os.environ["DOCKER_HOST"].removeprefix("unix://"))
    try:
        assert await DeploymentRecoveryService(maker, observer.observe_stopped).recover_once()
    finally:
        await observer.aclose()
    async with maker() as db:
        row = (
            await db.execute(
                text("SELECT status,sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
        ).one()
        assert row.status == "failed" and row.sandbox_id == identifier
        assert await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease")) is None
    async with maker.begin() as db:
        fresh = await DeploymentRepository(db).claim(lease.worker_id)
        assert fresh is not None and fresh.deployment_id == next_job
        assert fresh.generation > lease.generation


async def test_real_recorded_build_stop_preserves_active_revision(queue_world, owned_build):
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        await repo.enqueue(request)
        lease = await repo.claim(uuid4())
        assert lease is not None
    identifier = await owned_build(lease)
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.record_sandbox(lease, identifier)
        assert await repo.begin_probing(lease, DIGEST)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
        assert await repo.promote(lease)
    observer = DeploymentBuildCleanup(os.environ["DOCKER_HOST"].removeprefix("unix://"))
    try:
        assert await DeploymentRecoveryService(maker, observer.observe_stopped).recover_once()
    finally:
        await observer.aclose()
    async with maker() as db:
        assert await db.scalar(text("SELECT deployment_id FROM deployment_worker_lease")) is None
        assert (
            await db.scalar(
                text("SELECT active_revision FROM service_routes WHERE project_id=:id"),
                {"id": request.project_id},
            )
            == lease.deployment_id
        )
        row = (
            await db.execute(
                text("SELECT status,image_digest,sandbox_id FROM project_deployments WHERE id=:id"),
                {"id": lease.deployment_id},
            )
        ).one()
        assert (
            row.status == "active" and row.image_digest == DIGEST and row.sandbox_id == identifier
        )
