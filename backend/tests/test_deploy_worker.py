"""A4 worker step against real PostgreSQL fences; only fetch/scan/build I/O is substituted."""

import asyncio
import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_deployment_queue import deployment_engine as deployment_engine
from test_deployment_queue import deployment_postgres as deployment_postgres
from test_deployment_queue import deployment_project as deployment_project
from test_deployment_queue import deployment_session as deployment_session
from test_deployment_queue import queue_world as queue_world

from app.core.background import DeploymentWorkerTask
from app.core.config import get_settings
from app.repositories.deployment_repo import DeploymentRepository
from app.services.deployment.builder import BuildResult, FakeBuilder
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.worker import DeploymentWorker
from app.workers import a4
from app.workers.a4 import serve

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
ROW = text("SELECT status, reason_code FROM project_deployments ORDER BY created_at, id")
SLOT = text("SELECT deployment_id FROM deployment_worker_lease")


class Fetch:
    """Stands in for the GitHub download; records where the source landed."""

    def __init__(self, error: BaseException | None = None):
        self.error = error
        self.dests: list[Path] = []

    async def __call__(self, repo_url: str, commit_sha: str, dest: Path) -> None:
        dest.mkdir(parents=True)
        (dest / "Dockerfile").write_text("FROM scratch\n")
        self.dests.append(dest)
        if self.error:
            raise self.error


class BlockingBuilder:
    def __init__(self):
        self.started = asyncio.Event()

    async def build(self, request):
        self.started.set()
        await asyncio.Event().wait()


class Rejecting:
    async def scan(self, root: Path):
        raise DeployRejectedError("secret_found", "rule at file:1")


@pytest_asyncio.fixture(loop_scope="module")
async def world(queue_world):
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE service_routes"))
        await DeploymentRepository(db).enqueue(request)
    return maker


def worker(maker, builder=None, scanners=(), fetch=None, lease_seconds=60):
    return DeploymentWorker(
        maker, builder or FakeBuilder(), list(scanners), fetch or Fetch(), lease_seconds
    )


async def rows(maker):
    async with maker() as db:
        return [tuple(r) for r in (await db.execute(ROW)).all()], await db.scalar(SLOT)


async def test_idle_queue_returns_false(queue_world):
    maker, _ = queue_world
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE deployment_worker_lease, project_deployments"))
        await db.execute(text("INSERT INTO deployment_worker_lease (slot) VALUES (1)"))
    assert await worker(maker).run_once() is False


async def test_built_job_is_terminal_and_slot_released(world):
    builder, fetch = FakeBuilder(result=BuildResult("img:1", 5)), Fetch()
    assert await worker(world, builder, fetch=fetch).run_once() is True
    assert await rows(world) == ([("failed", "runtime_unavailable")], None)
    assert len(builder.requests) == 1
    assert not fetch.dests[0].exists() and not builder.requests[0].context_dir.exists()


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"scanners": [Rejecting()]}, "secret_found"),
        ({"builder": FakeBuilder(error=DeployRejectedError("image_too_large"))}, "image_too_large"),
        ({"fetch": Fetch(DeployRejectedError("source_fetch_failed"))}, "source_fetch_failed"),
        ({"fetch": Fetch(RuntimeError("boom"))}, "internal_error"),
    ],
)
async def test_refusals_store_stable_reason_and_clean_tempdir(world, kwargs, code):
    fetch = kwargs.setdefault("fetch", Fetch())
    assert await worker(world, **kwargs).run_once() is True
    assert await rows(world) == ([("failed", code)], None)
    assert not fetch.dests[0].exists()


async def test_invalid_manifest_is_refused(world):
    class BadManifest(Fetch):
        async def __call__(self, repo_url, commit_sha, dest):
            await super().__call__(repo_url, commit_sha, dest)
            (dest / "agentspore.toml").write_text("nope = [")

    assert await worker(world, fetch=BadManifest()).run_once() is True
    assert (await rows(world))[0] == [("failed", "manifest_invalid")]


async def test_cancel_ends_row_terminal_and_removes_tempdir(world):
    builder, fetch = BlockingBuilder(), Fetch()
    task = asyncio.create_task(worker(world, builder, fetch=fetch).run_once())
    await builder.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await rows(world) == ([("failed", "worker_shutdown")], None)
    assert not fetch.dests[0].exists()


async def test_sigterm_during_blocked_build_ends_terminal(world, monkeypatch):
    monkeypatch.setattr(get_settings(), "a4_deploy_enabled", True)
    builder, fetch = BlockingBuilder(), Fetch()
    task = DeploymentWorkerTask(worker(world, builder, fetch=fetch))
    serving = asyncio.create_task(serve(task))
    await builder.started.wait()
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(serving, 5)
    assert await rows(world) == ([("failed", "worker_shutdown")], None)
    assert not fetch.dests[0].exists()


def test_entrypoint_installs_log_redaction(monkeypatch):
    monkeypatch.setattr(a4, "serve", AsyncMock())
    monkeypatch.setattr(a4, "build_task", lambda: None)
    logger.configure(patcher=lambda record: None)
    a4.main()
    seen = []
    handler = logger.add(seen.append, format="{message}")
    try:
        logger.info("connect redis://:hunter2hunter2@cache:6379")
    finally:
        logger.remove(handler)
    assert "hunter2hunter2" not in seen[0]


async def test_lost_lease_stops_build_without_terminal_write(world):
    builder, fetch = BlockingBuilder(), Fetch()
    task = asyncio.create_task(worker(world, builder, fetch=fetch, lease_seconds=1).run_once())
    await builder.started.wait()
    async with world.begin() as db:
        await db.execute(
            text(
                "UPDATE deployment_worker_lease SET expires_at = clock_timestamp() - interval '1s'"
            )
        )
    assert await asyncio.wait_for(task, 5) is True
    assert (await rows(world))[0] == [("building", None)]
    assert not fetch.dests[0].exists()


async def test_expired_build_is_recovered_before_next_claim(world):
    async with world.begin() as db:
        assert await DeploymentRepository(db).claim(uuid4()) is not None
        await db.execute(
            text(
                "UPDATE deployment_worker_lease SET expires_at = clock_timestamp() - interval '1s'"
            )
        )
    assert await worker(world).run_once() is True
    assert await rows(world) == ([("failed", "lease_expired")], None)
    assert await worker(world).run_once() is False


async def test_live_holder_is_never_recovered_or_replaced(world):
    async with world.begin() as db:
        lease = await DeploymentRepository(db).claim(uuid4())
    assert await worker(world).run_once() is False
    assert await rows(world) == ([("building", None)], lease.deployment_id)


async def test_concurrent_claims_serialize_on_slot_lock(queue_world, deployment_engine):
    maker, (first, second) = queue_world
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE service_routes"))
        await db.execute(text("TRUNCATE project_deployments CASCADE"))
        await db.execute(text("INSERT INTO deployment_worker_lease (slot) VALUES (1)"))
        repo = DeploymentRepository(db)
        await repo.enqueue(first)
        await repo.enqueue(second)
    wide = create_async_engine(deployment_engine.url, pool_size=4, max_overflow=0)
    holder, rival = AsyncSession(wide), AsyncSession(wide)
    first_lease = await DeploymentRepository(holder).claim(uuid4())
    rival_claim = asyncio.create_task(DeploymentRepository(rival).claim(uuid4()))
    waiting = text(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE wait_event_type = 'Lock' AND query LIKE '%deployment_worker_lease%'"
    )
    async with AsyncSession(wide) as probe:
        for _ in range(100):
            if await probe.scalar(waiting):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("rival claim never waited on the slot lock")
    assert not rival_claim.done()
    await holder.commit()
    assert await asyncio.wait_for(rival_claim, 5) is None
    await rival.commit()
    assert first_lease is not None
    async with maker() as db:
        counts = (
            await db.execute(text("SELECT status, count(*) FROM project_deployments GROUP BY 1"))
        ).all()
    assert sorted(tuple(c) for c in counts) == [("building", 1), ("queued", 1)]
    await holder.close()
    await rival.close()
    await wide.dispose()


async def test_fail_is_refused_once_the_fence_expired(world):
    async with world.begin() as db:
        lease = await DeploymentRepository(db).claim(uuid4())
        await db.execute(
            text(
                "UPDATE deployment_worker_lease SET expires_at = clock_timestamp() - interval '1s'"
            )
        )
    async with world.begin() as db:
        assert await DeploymentRepository(db).fail(lease, "late") is False
    assert await rows(world) == ([("building", None)], lease.deployment_id)


async def test_fail_is_refused_for_a_foreign_fence(world):
    async with world.begin() as db:
        lease = await DeploymentRepository(db).claim(uuid4())
    stranger = lease.model_copy(update={"worker_id": uuid4()})
    async with world.begin() as db:
        assert await DeploymentRepository(db).fail(stranger, "late") is False
    assert await rows(world) == ([("building", None)], lease.deployment_id)
