"""A4 health uses only canonical revision HTTP and bounded transport cleanup."""

import asyncio
import importlib
from uuid import UUID

import httpx
import pytest

from app.schemas.deployments import DeploymentLease

pytestmark = pytest.mark.asyncio


class HealthStream(httpx.AsyncByteStream):
    """Expose accidental body consumption and cancellable stalled cleanup."""

    def __init__(self, stall_close: bool = False):
        self.stall_close = stall_close
        self.read = False
        self.close_started = asyncio.Event()
        self.close_finished = asyncio.Event()

    async def __aiter__(self):
        self.read = True
        yield b"synthetic body fragment"
        await asyncio.Event().wait()

    async def aclose(self):
        self.close_started.set()
        try:
            if self.stall_close:
                await asyncio.Event().wait()
        finally:
            self.close_finished.set()


class ClosingTransport(httpx.MockTransport):
    """Track ownership of a stalled but cancellation-cooperative HTTP transport."""

    def __init__(self):
        super().__init__(lambda request: httpx.Response(200, request=request))
        self.close_started = asyncio.Event()
        self.close_finished = asyncio.Event()

    async def aclose(self):
        self.close_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.close_finished.set()


@pytest.fixture
def lease() -> DeploymentLease:
    return DeploymentLease(
        deployment_id=UUID("00000000-0000-4000-8000-000000000001"),
        worker_id=UUID("00000000-0000-4000-8000-000000000002"),
        generation=3,
    )


@pytest.fixture
def health_probe():
    return importlib.import_module("app.services.deployment_health").DeploymentHealthProbe


@pytest.mark.parametrize("status", [200, 301, 500])
async def test_canonical_health_never_redirects_or_carries_cookies(health_probe, lease, status):
    requests = []
    streams = []

    def handler(request):
        requests.append(request)
        stream = HealthStream()
        streams.append(stream)
        return httpx.Response(
            status,
            stream=stream,
            headers={"Location": "http://other.invalid/", "Set-Cookie": "untrusted=value"},
        )

    probe = health_probe(transport=httpx.MockTransport(handler))
    try:
        assert await probe(lease) == status
        assert not list(probe.client.cookies.jar)
        assert await probe(lease) == status
        assert not list(probe.client.cookies.jar)
    finally:
        await probe.aclose()
    assert len(requests) == 2
    for request in requests:
        assert str(request.url) == f"http://svc-{lease.deployment_id.hex}:8080/health"
        assert request.method == "GET"
        assert not set(request.headers) & {
            "authorization",
            "cookie",
            "x-api-key",
            "x-runner-key",
            "x-forwarded-user",
        }
    assert all(not stream.read and stream.close_finished.is_set() for stream in streams)


@pytest.mark.parametrize("mode", ["headers", "network", "close"])
async def test_stalled_or_failed_http_returns_failure_within_two_seconds(health_probe, lease, mode):
    stream = HealthStream(stall_close=mode == "close")

    async def handler(request):
        if mode == "headers":
            await asyncio.Event().wait()
        if mode == "network":
            raise httpx.ConnectError("synthetic_unreachable", request=request)
        return httpx.Response(200, stream=stream)

    probe = health_probe(transport=httpx.MockTransport(handler))
    try:
        async with asyncio.timeout(2):
            assert await probe(lease) == 0
    finally:
        await probe.aclose()
    if mode == "close":
        assert stream.close_started.is_set() and stream.close_finished.is_set()


@pytest.mark.parametrize("phase", ["headers", "close"])
async def test_caller_cancellation_propagates_and_finishes_response_cleanup(
    health_probe, lease, phase
):
    stream = HealthStream(stall_close=True)
    headers_started = asyncio.Event()

    async def handler(request):
        headers_started.set()
        if phase == "headers":
            await asyncio.Event().wait()
        return httpx.Response(200, stream=stream)

    probe = health_probe(transport=httpx.MockTransport(handler))
    task = asyncio.create_task(probe(lease))
    try:
        started = headers_started if phase == "headers" else stream.close_started
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)
        if phase == "close":
            assert stream.close_finished.is_set()
    finally:
        if not task.done():
            task.cancel()
        await probe.aclose()


async def test_transport_shutdown_is_bounded_and_owned(health_probe):
    transport = ClosingTransport()
    probe = health_probe(transport=transport)
    async with asyncio.timeout(0.5):
        await probe.aclose()
    assert transport.close_started.is_set() and transport.close_finished.is_set()
