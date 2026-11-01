"""Idle cleanup through real PostgreSQL coordination and the launcher HTTP boundary."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_deployment_queue import deployment_engine as deployment_engine
from test_deployment_queue import deployment_postgres as deployment_postgres
from test_deployment_queue import deployment_project as deployment_project
from test_deployment_queue import deployment_session as deployment_session
from test_deployment_queue import queue_world as queue_world

from app.core.config import get_settings
from app.repositories.deployment_repo import DeploymentRepository
from app.services.deployment.launcher_client import LauncherClient
from app.services.deployment.runtime_cleanup import RuntimeCleanup

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
DIGEST = "sha256:" + "d" * 64


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "a4_deploy_enabled", True)


@pytest_asyncio.fixture(loop_scope="module")
async def rows(queue_world):  # noqa: F811 (pytest injects the imported queue fixture)
    maker, (request, _) = queue_world

    async def insert(status, age=0, project=None):
        dep = uuid4()
        ns = f"a4-{dep.hex}-1"
        async with maker.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO project_deployments "
                    "(id, project_id, idempotency_key, commit_sha, repository_url, status, "
                    "generation, sandbox_namespace, sandbox_id, image_digest, activated_at) "
                    "VALUES (:id, :project, :key, :sha, 'https://example.test/r', :status, "
                    "1, :ns, :sandbox, :digest, clock_timestamp()-make_interval(secs=>:age))"
                ),
                {
                    "id": dep,
                    "project": project or request.project_id,
                    "key": uuid4(),
                    "sha": "a" * 40,
                    "status": status,
                    "ns": ns,
                    "sandbox": "container-" + ns,
                    "digest": DIGEST,
                    "age": age,
                },
            )
        return dep, ns

    return maker, insert


@pytest.fixture
def boundary():
    class Boundary:
        def __init__(self):
            self.inventory = {"runtimes": [], "networks": []}
            self.calls = []
            self.reply = None

        async def __call__(self, req):
            self.calls.append((req.method, req.url.path, json.loads(req.content or b"null")))
            if self.reply is not None:
                return self.reply
            if req.method == "GET":
                return httpx.Response(200, json=self.inventory)
            return httpx.Response(
                200, json={"removed": []} if req.url.path.endswith("gc") else {"ok": True}
            )

    return Boundary()


@pytest_asyncio.fixture(loop_scope="module")
async def cleanup(rows, boundary, enabled):
    client = LauncherClient("http://launcher", transport=httpx.MockTransport(boundary))
    yield RuntimeCleanup(rows[0], client)
    await client.aclose()


async def test_retire_after_30s_not_before(rows, boundary, cleanup):
    maker, add = rows
    old, ns = await add("superseded", 100)
    new, _ = await add("active", 0)
    boundary.inventory["runtimes"] = [{"namespace": ns}]
    await cleanup.run_once()
    assert not any(method == "DELETE" for method, _, _ in boundary.calls)
    async with maker.begin() as db:
        await db.execute(
            text(
                "UPDATE project_deployments SET "
                "activated_at=clock_timestamp()-interval '31 seconds' "
                "WHERE id=:id"
            ),
            {"id": new},
        )
    assert await cleanup.run_once()
    assert ("DELETE", f"/v1/runtimes/{ns}", None) in boundary.calls
    async with maker() as db:
        assert await db.scalar(
            text("SELECT retired_at IS NOT NULL FROM project_deployments WHERE id=:id"), {"id": old}
        )


async def test_image_gc_keeps_two_successful(rows, boundary, cleanup):
    _, add = rows
    await add("superseded", 300)
    _, prior = await add("superseded", 200)
    _, active = await add("active", 100)
    await cleanup.run_once()
    keep = next(body["keep"] for _, path, body in boundary.calls if path.endswith("gc"))
    assert set(keep) == {f"127.0.0.1:5000/{ns}@{DIGEST}" for ns in (prior, active)}


async def test_orphans_and_failed_removed_active_and_grace_preserved(rows, boundary, cleanup):
    _, add = rows
    _, active = await add("active")
    _, failed = await add("failed")
    orphan = f"a4-{uuid4().hex}-1"
    boundary.inventory = {
        "runtimes": [{"namespace": active}, {"namespace": failed}],
        "networks": [{"namespace": orphan}],
    }
    await cleanup.run_once()
    deleted = {path.rsplit("/", 1)[-1] for method, path, _ in boundary.calls if method == "DELETE"}
    assert deleted == {failed, orphan}


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(503),
        httpx.Response(200, json={"runtimes": []}),
        httpx.Response(200, json={"runtimes": [], "networks": [{"namespace": "../../foreign"}]}),
    ],
)
async def test_unknown_inventory_never_deletes(boundary, cleanup, reply):
    boundary.reply = reply
    assert not await cleanup.run_once()
    assert all(method == "GET" for method, _, _ in boundary.calls)


async def test_invalid_delete_ack_never_marks_retired(rows, boundary, cleanup):
    maker, add = rows
    old, ns = await add("superseded", 100)
    await add("active", 40)
    boundary.reply = httpx.Response(200, json={"runtimes": [{"namespace": ns}], "networks": []})
    assert not await cleanup.run_once()
    async with maker() as db:
        assert await db.scalar(
            text("SELECT retired_at IS NULL FROM project_deployments WHERE id=:id"), {"id": old}
        )


async def test_flag_off_no_launcher_calls(boundary, cleanup, monkeypatch):
    monkeypatch.setattr(get_settings(), "a4_deploy_enabled", False)
    assert not await cleanup.run_once()
    assert boundary.calls == []


async def test_live_or_expired_lease_skips_cleanup(queue_world, boundary, cleanup):  # noqa: F811 (pytest injects the imported queue fixture)
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        await DeploymentRepository(db).enqueue(request)
        assert await DeploymentRepository(db).claim(uuid4())
    assert not await cleanup.run_once()
    assert boundary.calls == []
    async with maker.begin() as db:
        await db.execute(
            text(
                "UPDATE deployment_worker_lease SET "
                "expires_at=clock_timestamp()-interval '1 second'"
            )
        )
    assert not await cleanup.run_once()
    assert boundary.calls == []


async def test_claim_waits_for_gc_transaction(rows, queue_world, enabled):  # noqa: F811 (pytest injects the imported queue fixture)
    maker, _ = rows
    _, (request, _) = queue_world
    reached, release, claiming = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def boundary(req):
        if req.method == "GET":
            return httpx.Response(200, json={"runtimes": [], "networks": []})
        reached.set()
        await release.wait()
        return httpx.Response(200, json={"removed": []})

    async def claim():
        async with maker.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout = '200ms'"))
            claiming.set()
            return await DeploymentRepository(db).claim(uuid4())

    async with maker.begin() as db:
        await DeploymentRepository(db).enqueue(request)
    async with asyncio.timeout(5):
        client = LauncherClient("http://launcher", transport=httpx.MockTransport(boundary))
        sweep = asyncio.create_task(RuntimeCleanup(maker, client).run_once())
        await reached.wait()
        claiming_task = asyncio.create_task(claim())
        await claiming.wait()
        try:
            with pytest.raises(DBAPIError, match="lock timeout"):
                await claiming_task
        finally:
            release.set()
            await sweep
            await client.aclose()
        assert await claim() is not None


async def test_cancellation_rolls_back_retirement_and_releases_slot(rows, enabled):
    maker, add = rows
    old, ns = await add("superseded", 100)
    await add("active", 40)
    deleting = asyncio.Event()

    async def boundary(req):
        if req.method == "GET":
            return httpx.Response(200, json={"runtimes": [{"namespace": ns}], "networks": []})
        deleting.set()
        await asyncio.Event().wait()

    client = LauncherClient("http://launcher", transport=httpx.MockTransport(boundary))
    async with asyncio.timeout(5):
        pending = asyncio.create_task(RuntimeCleanup(maker, client).run_once())
        await deleting.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with maker.begin() as db:
            await db.execute(text("SELECT slot FROM deployment_worker_lease FOR UPDATE NOWAIT"))
            assert await db.scalar(
                text("SELECT retired_at IS NULL FROM project_deployments WHERE id=:id"), {"id": old}
            )
    await client.aclose()


async def test_gc_invalid_response_and_total_deadline(rows, enabled, monkeypatch):
    calls = []

    async def boundary(req):
        calls.append(req.method)
        if req.method == "GET":
            return httpx.Response(200, json={"runtimes": [], "networks": []})
        return httpx.Response(200, json={"ok": True})

    client = LauncherClient("http://launcher", transport=httpx.MockTransport(boundary))
    cleanup = RuntimeCleanup(rows[0], client)
    assert not await cleanup.run_once()
    assert calls == ["GET", "POST"]
    monkeypatch.setattr(cleanup, "deadline_seconds", 0)
    assert not await cleanup.run_once()
    await client.aclose()
