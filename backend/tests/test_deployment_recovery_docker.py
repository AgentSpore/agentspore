"""Real owned Docker stop observations persisted by real PostgreSQL recovery."""

import asyncio
import os
import re
from functools import partial
from urllib.parse import urlsplit
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


async def create_owned_build(client, lease, owned, image, owner_labels):
    """Register the exact created identity before startup can fail."""
    labels = {
        **owner_labels,
        "com.agentspore.a4.namespace": lease.sandbox_namespace,
        "com.agentspore.a4.deployment": str(lease.deployment_id),
        "com.agentspore.a4.worker": str(lease.worker_id),
        "com.agentspore.a4.generation": str(lease.generation),
        "com.agentspore.a4.role": "build",
    }
    name = "recovery-build-" + uuid4().hex
    record = {"labels": labels, "id": None}
    owned[name] = record
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
        image=image,
        name=name,
        # PID 1 ignores SIGTERM unless it traps it; a bare sleep would make
        # `stop?t=1` take the full grace period, eating the observer's 1.8s budget.
        entrypoint=["/bin/sh", "-c"],
        command=['trap "exit 0" TERM; sleep 120 & wait'],
        user="65532",
        labels=labels,
        host_config=limits,
    )
    identifier = created["Id"]
    assert re.fullmatch(r"[a-f0-9]{64}", identifier)
    record["id"] = identifier
    assert await observe_owned_build(client, name, record) is not None
    # When the optional diagnostic harness is active:
    # The existing diagnostic plugin records this full ID before any startup.
    await asyncio.to_thread(client.start, identifier)
    row = await observe_owned_build(client, name, record)
    assert row is not None
    assert row["State"] == "running"
    return identifier


async def observe_owned_build(client, name, record):
    """Reconcile only the exact pending name and its independently verified owner."""
    rows = await asyncio.to_thread(
        client.containers, all=True, trunc=False, filters={"name": ["^/" + name + "$"]}
    )
    if not rows:
        return None
    assert len(rows) == 1 and rows[0]["Names"] == ["/" + name]
    row = rows[0]
    assert re.fullmatch(r"[a-f0-9]{64}", row["Id"])
    assert record["id"] is None or row["Id"] == record["id"]
    assert all(row["Labels"].get(key) == value for key, value in record["labels"].items())
    return row


async def remove_owned_builds(client, owned):
    """Collect every pending create, preserving all cleanup failures."""
    failures = []
    for name, record in owned.items():
        try:
            row = await observe_owned_build(client, name, record)
            if row is not None:
                await asyncio.to_thread(client.remove_container, row["Id"], force=True, v=True)
        except Exception as error:
            failures.append(error)
    if failures:
        raise ExceptionGroup("Owned build cleanup failed", failures)


@pytest_asyncio.fixture(loop_scope="module")
async def owned_build(deployment_postgres):
    """Own synthetic builds independently of optional diagnostic harness settings."""
    endpoint = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    socket = urlsplit(endpoint)
    if (
        socket.scheme != "unix"
        or socket.netloc
        or not socket.path.startswith("/")
        or socket.query
        or socket.fragment
    ):
        raise ValueError("Recovery fixture requires an absolute Unix Docker socket")
    image = deployment_postgres.get_wrapped_container().attrs["Image"]
    owner_labels = {"com.agentspore.recovery-test.owner": uuid4().hex}
    if marker := os.environ.get("AF35_RUN_ID"):
        owner_labels["af35.owner"] = marker
    client = await asyncio.to_thread(docker.APIClient, base_url=endpoint, version="auto", timeout=3)
    owned = {}

    try:
        yield (
            partial(
                create_owned_build, client, owned=owned, image=image, owner_labels=owner_labels
            ),
            socket.path,
        )
    finally:
        try:
            await remove_owned_builds(client, owned)
        finally:
            await asyncio.to_thread(client.close)


async def test_real_pre_id_stop_adopts_full_id_and_unlocks_queue(recovery_world, owned_build):
    maker, lease, next_job = recovery_world
    create, socket = owned_build
    identifier = await create(lease)
    observer = DeploymentBuildCleanup(socket)
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
    create, socket = owned_build
    identifier = await create(lease)
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.record_sandbox(lease, identifier)
        assert await repo.begin_probing(lease, DIGEST)
        for _ in range(3):
            assert await repo.record_health(lease, 200)
        assert await repo.promote(lease)
    observer = DeploymentBuildCleanup(socket)
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
