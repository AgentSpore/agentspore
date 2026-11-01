"""Worker flow with `A4_RUNTIME=launcher` against real PostgreSQL fences and a fake launcher."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from test_deploy_worker import Fetch as Fetch
from test_deployment_queue import deployment_engine as deployment_engine
from test_deployment_queue import deployment_postgres as deployment_postgres
from test_deployment_queue import deployment_project as deployment_project
from test_deployment_queue import deployment_session as deployment_session
from test_deployment_queue import queue_world as queue_world

from app.core.config import get_settings
from app.repositories.deployment_repo import DeploymentRepository
from app.services.deployment.builder import BuildResult, FakeBuilder
from app.services.deployment.launcher_client import LauncherClient
from app.services.deployment.launcher_runtime import LauncherRuntime
from app.services.deployment.worker import DeploymentWorker
from app.services.deployment_promotion import DeploymentPromotionService
from app.workers import a4

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
ROW = text("SELECT status, reason_code FROM project_deployments")
DIGEST = "sha256:" + "d" * 64


class FakeLauncher:
    """Records calls; `fail_on` maps a route verb to a response or exception."""

    def __init__(self, fail_on=None):
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self.fail_on = fail_on or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        tail = request.url.path.rsplit("/", 1)[-1]
        verb = (
            "list"
            if request.method == "GET"
            else tail
            if tail in {"start", "stop"}
            else request.method
        )
        self.calls.append((request.method, verb))
        if verb in self.fail_on:
            outcome = self.fail_on[verb]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if request.method == "PUT":
            self.bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"container_id": "a" * 64})
        return httpx.Response(200, json={"ok": True})


@pytest_asyncio.fixture(loop_scope="module")
async def world(queue_world):  # noqa: F811 (fixture of the same name from test_deploy_worker would shadow the imported queue_world)
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE service_routes"))
        await DeploymentRepository(db).enqueue(request)
    return maker


async def run(world, launcher, health=200, enable=True):
    builder = FakeBuilder(
        result=BuildResult(image_ref=f"127.0.0.1:5000/a4-x@{DIGEST}", image_bytes=1)
    )
    worker = DeploymentWorker(world, builder, [], Fetch())
    if enable:
        client = LauncherClient("http://l:8080", transport=httpx.MockTransport(launcher))
        promotion = DeploymentPromotionService(world, AsyncMock(return_value=health))
        worker.attach_runtime(LauncherRuntime(world, client, promotion, "https://agentspore.com/"))
    assert await worker.run_once() is True
    async with world() as db:
        return [tuple(r) for r in (await db.execute(ROW)).all()]


async def test_off_path_is_runtime_unavailable_and_never_calls_launcher(world):
    launcher = FakeLauncher()
    assert await run(world, launcher, enable=False) == [("failed", "runtime_unavailable")]
    assert launcher.calls == []


async def test_launcher_happy_flow_activates_and_routes(world):
    launcher = FakeLauncher()
    assert await run(world, launcher) == [("active", None)]
    assert [c for c in launcher.calls if c[1] != "list"] == [("PUT", "PUT"), ("POST", "start")]
    body = launcher.bodies[0]
    assert body["image"].endswith(DIGEST) and body["generation"] == 1
    async with world() as db:
        route = await db.scalar(text("SELECT active_revision::text FROM service_routes"))
    assert route.replace("-", "") == body["deployment_id"]


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (httpx.Response(409, json={"reason": "namespace_owned"}), "runtime_conflict"),
        (httpx.Response(429, json={"reason": "runtime_cap"}), "runtime_capacity"),
        (httpx.Response(502, json={"reason": "docker_error"}), "runtime_unavailable"),
        (httpx.ReadTimeout("slow"), "runtime_unavailable"),
    ],
)
async def test_put_failure_fails_row_and_cleans_up(world, outcome, code):
    launcher = FakeLauncher({"PUT": outcome})
    assert await run(world, launcher) == [("failed", code)]
    assert ("DELETE", "DELETE") in launcher.calls
    assert ("POST", "start") not in launcher.calls


async def test_start_failure_deletes_runtime_and_leaves_row_to_recovery(world):
    launcher = FakeLauncher({"start": httpx.Response(502, json={"reason": "docker_error"})})
    assert await run(world, launcher) == [("recovering", "health_failed")]
    assert ("DELETE", "DELETE") in launcher.calls


async def test_unhealthy_service_never_activates(world):
    launcher = FakeLauncher()
    assert await run(world, launcher, health=503) == [("recovering", "health_failed")]
    async with world() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_routes")) == 0


async def test_unhealthy_replacement_preserves_active_revision(world, request):
    assert await run(world, FakeLauncher()) == [("active", None)]
    async with world() as db:
        previous = await db.scalar(text("SELECT active_revision FROM service_routes"))
        namespace = await db.scalar(text("SELECT sandbox_namespace FROM project_deployments"))
    inventory = FakeLauncher(
        {
            "list": httpx.Response(
                200,
                json={
                    "runtimes": [
                        {"namespace": namespace, "container_id": "a" * 64, "state": "running"}
                    ]
                },
            )
        }
    )
    assert await run(world, inventory) == [("active", None)]
    assert inventory.calls == [("GET", "list")]
    _, (queued, _) = request.getfixturevalue("queue_world")
    async with world.begin() as db:
        replacement = await DeploymentRepository(db).enqueue(
            queued.model_copy(update={"idempotency_key": uuid4(), "commit_sha": "b" * 40})
        )
    outcomes = await run(world, FakeLauncher(), health=503)
    assert sorted(outcomes) == [("active", None), ("recovering", "health_failed")]
    async with world() as db:
        assert await db.scalar(text("SELECT active_revision FROM service_routes")) == previous
        rows = await db.execute(text("SELECT id, status FROM project_deployments"))
        assert dict(rows.all()) == {previous: "active", replacement: "recovering"}


@pytest.mark.parametrize("lose_after", ["PUT", "start"])
async def test_runtime_lost_fence_cleans_container_without_promotion(world, lose_after):
    launcher = FakeLauncher()

    async def respond(request):
        response = launcher(request)
        if launcher.calls[-1][1] == lose_after:
            async with world.begin() as db:
                await db.execute(
                    text(
                        "UPDATE deployment_worker_lease "
                        "SET expires_at = clock_timestamp() - interval '1 second'"
                    )
                )
        return response

    assert await run(world, respond) == [("building", None)]
    assert launcher.calls[-1] == ("DELETE", "DELETE")
    assert (("POST", "start") in launcher.calls) is (lose_after == "start")
    async with world() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_routes")) == 0
        assert await db.scalar(text("SELECT sandbox_id FROM project_deployments")) == (
            "a" * 64 if lose_after == "start" else None
        )


async def test_build_task_wires_runtime_only_when_switch_is_launcher(monkeypatch):
    for mode, expected in (("off", False), ("launcher", True)):
        monkeypatch.setattr(get_settings(), "a4_runtime", mode)
        assert (a4.build_task().worker.runtime is not None) is expected
