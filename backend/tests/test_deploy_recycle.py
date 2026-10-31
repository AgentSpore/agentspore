"""Daemon recycle through the restart gate, against an httpx MockTransport standing in for it."""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.deployment.builder import BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.buildkit import builder_from_settings
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.recycle import DaemonRecycler, RecyclingBuilder

BASE = "/v1.44/containers/a4-builder-buildkitd-1"


def state(running=True, health="healthy"):
    return httpx.Response(200, json={"State": {"Running": running, "Health": {"Status": health}}})


class Gate:
    """Records requests; `inspect` answers successive GET .../json calls."""

    def __init__(self, inspect=(), restart_status=204):
        self.calls: list[tuple[str, str, str]] = []
        self.inspect = list(inspect)
        self.restart_status = restart_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.url.query.decode()))
        if request.method == "POST":
            assert request.content == b""
            return httpx.Response(self.restart_status)
        return self.inspect.pop(0) if len(self.inspect) > 1 else self.inspect[0]


def recycler(gate: Gate, **kwargs) -> DaemonRecycler:
    return DaemonRecycler(
        "http://gate",
        transport=httpx.MockTransport(gate),
        poll_interval=0.0,
        **kwargs,
    )


async def test_recycle_restarts_then_waits_for_running_healthy():
    gate = Gate([state(health="starting"), state(running=False), state()])
    r = recycler(gate)
    await r.recycle()
    assert gate.calls[0] == ("POST", f"{BASE}/restart", "t=5")
    assert [c[:2] for c in gate.calls[1:]] == [("GET", f"{BASE}/json")] * 3
    assert r.dirty is False


@pytest.mark.parametrize(
    "gate,kwargs",
    [
        (Gate([state()], restart_status=403), {}),
        (Gate([state()], restart_status=500), {}),
        (Gate([httpx.Response(404)]), {}),
        (Gate([httpx.Response(200, text="not json")]), {}),
        (Gate([state(health="unhealthy")]), {"ready_timeout": 0.0}),
    ],
)
async def test_failed_recycle_logs_leaves_daemon_dirty_and_refuses_builds(gate, kwargs):
    r = recycler(gate, **kwargs)
    await r.recycle()
    assert r.dirty is True
    with pytest.raises(DeployRejectedError) as exc:
        await r.ensure_clean()
    assert exc.value.reason_code == "tool_failed"


async def test_unreachable_gate_is_a_failed_recycle_not_a_crash():
    def refuse(request):
        raise httpx.ConnectError("no route")

    r = DaemonRecycler("http://gate", transport=httpx.MockTransport(refuse))
    await r.recycle()
    assert r.dirty is True


async def test_fresh_worker_recycles_before_the_first_build_and_skips_when_clean():
    gate = Gate([state()])
    r = recycler(gate)
    assert r.dirty is True
    await r.ensure_clean()
    assert r.dirty is False
    n = len(gate.calls)
    await r.ensure_clean()
    assert len(gate.calls) == n


async def test_recovered_gate_lets_the_next_build_through():
    gate = Gate([state()], restart_status=500)
    r = recycler(gate)
    with pytest.raises(DeployRejectedError):
        await r.ensure_clean()
    gate.restart_status = 204
    await r.ensure_clean()
    assert r.dirty is False


async def test_cancel_during_recycle_stays_dirty():
    async def hang(request):
        raise asyncio.CancelledError

    r = DaemonRecycler("http://gate", transport=httpx.MockTransport(hang))
    r.dirty = False
    with pytest.raises(BaseException):  # noqa: B017, PT011 (CancelledError is a BaseException)
        await r.recycle()
    assert r.dirty is True


def test_settings_wrap_the_buildkit_builder_and_leave_fake_alone():
    assert isinstance(builder_from_settings(Settings(a4_builder="fake")), FakeBuilder)
    built = builder_from_settings(Settings(a4_builder="buildkit"))
    assert isinstance(built, RecyclingBuilder)
    r = built.recycler
    assert isinstance(r, DaemonRecycler) and r.dirty is True
    assert r.gate_url == "http://a4-restart-gate:2375" and r.container == "a4-builder-buildkitd-1"


class SpyRecycler:
    def __init__(self, refuse: bool = False):
        self.events: list[str] = []
        self.refuse = refuse

    async def ensure_clean(self):
        self.events.append("ensure_clean")
        if self.refuse:
            raise DeployRejectedError("tool_failed", "buildkitd recycle failed, builds refused")

    async def recycle(self):
        self.events.append("recycle")


class StepBuilder(FakeBuilder):
    def __init__(self, events, error=None, block=None):
        super().__init__(result=BuildResult("img:1", 5), error=error)
        self.events, self.block = events, block

    async def build(self, request):
        self.events.append("build")
        if self.block:
            self.block.set()
            await asyncio.Event().wait()
        return await super().build(request)


REQUEST = BuildRequest(context_dir=Path("/ctx"), tag="t:1")


@pytest.mark.parametrize("error", [None, DeployRejectedError("build_failed"), RuntimeError("x")])
async def test_daemon_is_recycled_after_every_build_outcome(error):
    spy = SpyRecycler()
    builder = RecyclingBuilder(StepBuilder(spy.events, error), spy)
    if error:
        with pytest.raises(type(error)):
            await builder.build(REQUEST)
    else:
        await builder.build(REQUEST)
    assert spy.events == ["ensure_clean", "build", "recycle"]


async def test_cancelled_build_still_recycles_the_daemon():
    spy, started = SpyRecycler(), asyncio.Event()
    builder = RecyclingBuilder(StepBuilder(spy.events, block=started), spy)
    task = asyncio.create_task(builder.build(REQUEST))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spy.events == ["ensure_clean", "build", "recycle"]


async def test_dirty_daemon_refuses_the_build_and_never_reaches_the_inner_builder():
    spy = SpyRecycler(refuse=True)
    with pytest.raises(DeployRejectedError) as exc:
        await RecyclingBuilder(StepBuilder(spy.events), spy).build(REQUEST)
    assert exc.value.reason_code == "tool_failed"
    assert spy.events == ["ensure_clean"]
