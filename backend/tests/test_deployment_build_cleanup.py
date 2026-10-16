"""Trusted build cleanup observes exact Docker metadata before issuing proofs."""

import asyncio
import importlib
import json
from uuid import UUID

import httpx
import pytest

from app.schemas.deployments import (
    DeploymentLease,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)

pytestmark = pytest.mark.asyncio
BUILD = "a" * 64
PREFIX = "com.agentspore.a4."


class StalledClose(httpx.AsyncByteStream):
    """Model successful inventory data with blocked resource cleanup."""

    def __init__(self):
        self.finished = asyncio.Event()

    async def __aiter__(self):
        yield b"[]"

    async def aclose(self):
        try:
            await asyncio.Event().wait()
        finally:
            self.finished.set()


class Engine(httpx.MockTransport):
    """Replace only the Docker Engine HTTP boundary; preserve actual HTTPX requests."""

    def __init__(self, lease, mode="running"):
        super().__init__(self.respond)
        self.mode = mode
        self.requests = []
        self.row = {
            "Id": BUILD,
            "State": "running",
            "Labels": {
                PREFIX + "namespace": lease.sandbox_namespace,
                PREFIX + "deployment": str(lease.deployment_id),
                PREFIX + "worker": str(lease.worker_id),
                PREFIX + "generation": str(lease.generation),
                PREFIX + "role": "build",
            },
        }
        self.stream = StalledClose()
        if mode in {"exited", "created", "dead"}:
            self.row["State"] = mode
        if mode in {"lost_labels", "runtime", "wrong_fence"}:
            key = {"lost_labels": "namespace", "runtime": "role", "wrong_fence": "generation"}[mode]
            self.row["Labels"][PREFIX + key] = "other"

    async def respond(self, request):
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("synthetic_timeout", request=request)
        if self.mode == "truncated":
            raise httpx.RemoteProtocolError("synthetic_truncated", request=request)
        if self.mode == "slow_headers":
            await asyncio.Event().wait()
        if request.method == "POST":
            return self.stop_response(request)
        assert request.url.host == "docker"
        assert request.url.path == "/containers/json"
        assert request.url.params.get("all") == "true"
        filters = json.loads(request.url.params["filters"])
        if self.mode == "inventory301":
            return httpx.Response(301, headers={"Location": "http://other.invalid/"})
        failures = {
            "daemon_error": (500, b""),
            "wrong_shape": (200, b"{}"),
            "malformed": (200, b"{"),
            "oversize": (200, b" " * 65537),
        }
        if self.mode in failures:
            status, content = failures[self.mode]
            return httpx.Response(status, content=content)
        if self.mode == "slow_close":
            return httpx.Response(200, stream=self.stream)
        rows = [] if self.mode == "absent" else [self.row]
        if "id" in filters:
            assert filters == {"id": [BUILD]}
        else:
            assert set(filters) == {"label"} and len(filters["label"]) == 1
            key, value = filters["label"][0].split("=", 1)
            assert key == PREFIX + "namespace"
            rows = [
                row
                for row in rows
                if isinstance(row["Labels"], dict) and row["Labels"].get(key) == value
            ]
        if self.mode == "multiple":
            rows = [*rows, {**self.row, "Id": "b" * 64}]
        return httpx.Response(200, json=rows, headers={"Set-Cookie": "untrusted=value"})

    def stop_response(self, request):
        assert request.url.path == f"/containers/{BUILD}/stop"
        assert request.url.params.get("t") == "1"
        stop_status = {"stop404": 404, "stop500": 500, "stop301": 301}.get(self.mode, 204)
        if stop_status != 204:
            return httpx.Response(stop_status, headers={"Location": "http://other.invalid/"})
        if self.mode != "still_running":
            self.row["State"] = "exited"
        return httpx.Response(304 if self.mode == "already_stopped" else 204)


@pytest.fixture
def lease():
    return DeploymentLease(
        deployment_id=UUID("00000000-0000-4000-8000-000000000001"),
        worker_id=UUID("00000000-0000-4000-8000-000000000002"),
        generation=3,
    )


@pytest.fixture
def cleanup_adapter():
    return importlib.import_module("app.services.deployment_build_cleanup").DeploymentBuildCleanup


@pytest.mark.parametrize(
    "state", ["running", "exited", "created", "dead", "absent", "already_stopped"]
)
async def test_owned_build_requires_verified_stop(cleanup_adapter, lease, state):
    engine = Engine(lease, state)
    adapter = cleanup_adapter("/synthetic/docker.sock", transport=engine)
    try:
        proof = await adapter.observe_stopped(lease, BUILD)
    finally:
        await adapter.aclose()
    assert proof == SandboxStopProof(lease=lease, sandbox_id=BUILD)
    methods = [request.method for request in engine.requests]
    assert methods.count("POST") == (0 if state == "absent" else 1)
    assert methods.count("GET") >= 2
    assert all(request.url.host == "docker" for request in engine.requests)
    assert all(
        not {"cookie", "authorization", "x-api-key"} & set(request.headers)
        for request in engine.requests
    )


@pytest.mark.parametrize("recorded", [None, BUILD])
@pytest.mark.parametrize("mode", ["lost_labels", "runtime", "wrong_fence", "multiple"])
async def test_unowned_inventory_prevents_stop(cleanup_adapter, lease, recorded, mode):
    engine = Engine(lease, mode)
    adapter = cleanup_adapter("/synthetic/docker.sock", transport=engine)
    try:
        proof = await adapter.observe_stopped(lease, recorded)
    finally:
        await adapter.aclose()
    # Pre-ID absence covers only the canonical namespace under trusted atomic labelled creation.
    if recorded is None and mode == "lost_labels":
        assert isinstance(proof, SandboxNamespaceAbsenceProof)
    else:
        assert proof is None
    assert all(request.method == "GET" for request in engine.requests)


@pytest.mark.parametrize("mode", ["absent", "running"])
async def test_pre_id_proof_matches_observation(cleanup_adapter, lease, mode):
    engine = Engine(lease, mode)
    adapter = cleanup_adapter("/synthetic/docker.sock", transport=engine)
    try:
        proof = await adapter.observe_stopped(lease, None)
    finally:
        await adapter.aclose()
    if mode == "absent":
        assert proof == SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)
    else:
        assert proof == SandboxStopProof(lease=lease, sandbox_id=BUILD)
        assert any(request.method == "POST" for request in engine.requests)


@pytest.mark.parametrize(
    "mode",
    (
        "daemon_error timeout malformed wrong_shape truncated oversize still_running slow_close "
        "stop404 stop500 stop301 inventory301"
    ).split(),
)
async def test_engine_failure_produces_no_proof(cleanup_adapter, lease, mode):
    engine = Engine(lease, mode)
    adapter = cleanup_adapter("/synthetic/docker.sock", transport=engine)
    try:
        async with asyncio.timeout(10):
            assert await adapter.observe_stopped(lease, BUILD) is None
    finally:
        await adapter.aclose()
    if mode == "slow_close":
        assert engine.stream.finished.is_set()


async def test_cancellation_during_inventory_propagates(cleanup_adapter, lease):
    engine = Engine(lease, "slow_headers")
    adapter = cleanup_adapter("/synthetic/docker.sock", transport=engine)
    task = asyncio.create_task(adapter.observe_stopped(lease, BUILD))
    try:
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)
        assert not any(request.method == "POST" for request in engine.requests)
    finally:
        if not task.done():
            task.cancel()
        await adapter.aclose()


@pytest.mark.parametrize("invalid_id", ["short", "../runtime/stop"])
async def test_invalid_identity_makes_no_request(cleanup_adapter, lease, invalid_id):
    engine = Engine(lease)
    adapter = cleanup_adapter("/synthetic/docker.sock", transport=engine)
    try:
        assert await adapter.observe_stopped(lease, invalid_id) is None
    finally:
        await adapter.aclose()
    assert not engine.requests
