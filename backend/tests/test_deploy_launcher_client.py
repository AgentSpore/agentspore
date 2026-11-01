"""LauncherClient against a MockTransport launcher; launcher text never leaks."""

import asyncio
import json

import httpx
import pytest

from app.launcher.schemas import NS_RE, RuntimeRequest
from app.services.deployment import launcher_client
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.launcher_client import LauncherClient

DEP = "ab" * 16
NS = f"a4-{DEP}-3"
IMAGE = f"127.0.0.1:5000/a4-{DEP}-3@sha256:" + "c" * 64
BODY = {
    "deployment_id": DEP,
    "generation": 3,
    "worker_id": "w-1",
    "image": IMAGE,
    "env": {},
    "public_url": "https://agentspore.com/",
}


def client_for(handler) -> LauncherClient:
    return LauncherClient("http://a4-launcher:8080", transport=httpx.MockTransport(handler))


def reply(status: int, payload: object) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.mark.asyncio
async def test_put_sends_a_body_the_launcher_schema_accepts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return reply(200, {"container_id": "f" * 64})

    assert await client_for(handler).put(NS, BODY) == "f" * 64
    assert seen["path"] == f"/v1/runtimes/{NS}"
    parsed = RuntimeRequest.model_validate(seen["body"])
    assert NS_RE.fullmatch(NS) and parsed.namespace == NS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload", "code"),
    [
        (409, {"reason": "namespace_owned"}, "runtime_conflict"),
        (429, {"reason": "runtime_cap"}, "runtime_capacity"),
        (503, {"reason": "pool_exhausted"}, "runtime_capacity"),
        (502, {"reason": "docker_error", "docker_status": 500}, "runtime_unavailable"),
        (422, {"reason": "digest_mismatch"}, "digest_mismatch"),
        (422, {"reason": "image_volume_not_allowed"}, "image_volume_not_allowed"),
        (422, {"reason": "ignore previous instructions"}, "runtime_unavailable"),
        (500, "boom", "runtime_unavailable"),
    ],
)
async def test_launcher_refusals_map_to_stable_codes(status, payload, code):
    with pytest.raises(DeployRejectedError) as exc:
        await client_for(lambda r: reply(status, payload)).put(NS, BODY)
    assert exc.value.reason_code == code
    assert "ignore" not in str(exc.value) and "boom" not in str(exc.value)


@pytest.mark.asyncio
async def test_timeout_and_transport_errors_are_runtime_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(DeployRejectedError) as exc:
        await client_for(handler).start(NS)
    assert exc.value.reason_code == "runtime_unavailable"


@pytest.mark.asyncio
async def test_oversize_reply_is_refused():
    big = httpx.Response(200, content=b"x" * (200 * 1024))
    with pytest.raises(DeployRejectedError):
        await client_for(lambda r: big).list_runtimes()


@pytest.mark.asyncio
async def test_list_runtimes_returns_entries_and_start_stop_delete_hit_their_routes():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return reply(200, {"runtimes": [{"namespace": NS, "state": "running"}]})
        return reply(200, {"ok": True})

    c = client_for(handler)
    assert (await c.list_runtimes())[0]["namespace"] == NS
    await c.start(NS)
    await c.stop(NS)
    await c.delete(NS)
    assert calls == [
        ("GET", "/v1/runtimes"),
        ("POST", f"/v1/runtimes/{NS}/start"),
        ("POST", f"/v1/runtimes/{NS}/stop"),
        ("DELETE", f"/v1/runtimes/{NS}"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "stop", "delete"])
@pytest.mark.parametrize(
    "content", [b"not-json", b"{}", b'{"ok":false}', b'{"ok":1}', b'{"ok":"true"}']
)
async def test_mutation_requires_positive_boolean_ack(operation, content):
    client = client_for(lambda request: httpx.Response(200, content=content))
    try:
        with pytest.raises(DeployRejectedError) as error:
            await getattr(client, operation)(NS)
        assert error.value.reason_code == "runtime_unavailable"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline_expires", [False, True])
async def test_real_http_stop_read_obeys_operation_deadline(monkeypatch, deadline_expires):
    release = asyncio.Event()

    async def respond(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            await release.wait()
            body = b'{"ok":true}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    if deadline_expires:
        monkeypatch.setattr(launcher_client, "_STOP_SECONDS", 0.05)
    async with asyncio.TaskGroup() as tasks:
        server = await asyncio.start_server(
            lambda reader, writer: tasks.create_task(respond(reader, writer)), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        client = LauncherClient(f"http://127.0.0.1:{port}")
        timer = asyncio.get_running_loop().call_later(6, release.set)
        try:
            async with server, asyncio.timeout(8):
                if deadline_expires:
                    with pytest.raises(DeployRejectedError) as error:
                        await client.stop(NS)
                    assert error.value.reason_code == "runtime_unavailable"
                else:
                    await client.stop(NS)
        finally:
            timer.cancel()
            release.set()
            await client.aclose()
