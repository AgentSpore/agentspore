"""Public collector boundaries; fixtures match public API schemas."""

import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest
from agentspore_sdk.operator_snapshot import collect_snapshot

NOW = datetime(2026, 10, 21, 18, tzinfo=timezone.utc)
PROJECT = "159aaae9-1627-594b-bf8c-4b24b46f2191"


@pytest.fixture
def public_transport():
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path == "/api/v1/projects":
            data = [
                {
                    "id": PROJECT,
                    "title": "Demo",
                    "status": "deployed",
                    "repo_url": "https://github.com/AgentSpore/demo",
                    "deploy_url": "http://169.254.169.254/latest",
                    "email": "PRIVATE",
                }
            ]
        elif path.endswith("/usage"):
            data = {"uniques_7d": 0, "retention_d7": 0, "visitor_id": "PRIVATE"}
        elif path.endswith("/stats"):
            data = {"total_agents": 4, "api_key": "PRIVATE"}
        elif path.endswith("/events"):
            event = {
                "id": "e1",
                "type": "PushEvent",
                "repo": {"name": "AgentSpore/demo"},
                "created_at": NOW.isoformat(),
                "payload": {"body": "PRIVATE"},
            }
            data = [event, event]
        elif path.endswith("/repos"):
            data = [{"name": "demo", "private": False}]
        elif path.endswith("/runs"):
            data = {
                "workflow_runs": [
                    {
                        "id": 7,
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": NOW.isoformat(),
                        "actor": "PRIVATE",
                    }
                ]
            }
        else:
            data = {"secret": "PRIVATE"}
        return httpx.Response(200, json=data)

    return httpx.MockTransport(respond), requests


@pytest.mark.asyncio
async def test_public_projection_and_unverified_zero(public_transport):
    transport, requests = public_transport
    async with httpx.AsyncClient(transport=transport) as client:
        result = await collect_snapshot(client, NOW)
    assert "PRIVATE" not in json.dumps(result)
    assert all(
        r.method == "GET" and not r.headers.get("authorization") for r in requests
    )
    assert all(r.url.host != "169.254.169.254" for r in requests)
    usage = result["projects"]["value"][0]["usage"]["value"]
    assert usage["uniques_7d"] == {
        "value": None,
        "status": "unavailable",
        "reason": "instrumentation_not_verified",
        "observed_raw": 0,
    }
    assert usage["retention_d7"]["reason"] == "cohort_coverage_not_verified"
    assert len(result["github_events"]["value"]) == 1
    assert result["github_events"]["reason"] == "source_window_not_verified"
    assert result["window"]["until"] == NOW.isoformat()
    assert result["github_ci"]["value"][0]["status"] == "complete"


@pytest.mark.parametrize("mode", ["redirect", "oversized", "shape", "timeout", "rate"])
@pytest.mark.asyncio
async def test_unavailable_sources_never_become_zero(mode):
    seen = []

    def respond(request):
        seen.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("PRIVATE", request=request)
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/PRIVATE"})
        if mode == "oversized":
            return httpx.Response(200, content=b"x" * 1_048_577)
        return httpx.Response(429 if mode == "rate" else 200, json="PRIVATE")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        result = await collect_snapshot(client, NOW)
    assert result["projects"]["value"] is None
    assert result["github_repos"]["status"] == "unavailable"
    assert result["github_ci"]["status"] == "unavailable"
    assert "PRIVATE" not in json.dumps(result)
    assert all(r.url.host != "127.0.0.1" for r in seen)


@pytest.mark.asyncio
async def test_naive_clock_rejected(public_transport):
    async with httpx.AsyncClient(transport=public_transport[0]) as client:
        with pytest.raises(ValueError):
            await collect_snapshot(client, datetime(2026, 10, 21))
    assert public_transport[1] == []


@pytest.mark.asyncio
async def test_budget_pagination_and_malicious_repository():
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.path == "/api/v1/projects":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(UUID(int=i + 1)),
                        "title": {"email": "PRIVATE"},
                        "repo_url": f"https://github.com/AgentSpore/demo{i}",
                    }
                    for i in range(200)
                ],
            )
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": str(i),
                        "type": "PushEvent",
                        "repo": {"name": "AgentSpore/../../PRIVATE"},
                        "created_at": NOW.isoformat(),
                    }
                    for i in range(100)
                ],
            )
        if request.url.path.endswith("/repos"):
            return httpx.Response(200, json=[{"name": "demo", "private": False}] * 100)
        return httpx.Response(200, json={"workflow_runs": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        auth=("PRIVATE", "PRIVATE"),
        cookies={"session": "PRIVATE"},
    ) as client:
        result = await collect_snapshot(client, NOW)
    assert sum(r.url.host == "api.github.com" for r in seen) <= 55
    assert all(
        not r.headers.get("authorization") and not r.headers.get("cookie") for r in seen
    )
    for source in ("projects", "github_events", "github_repos", "github_ci"):
        assert result[source]["status"] == "partial"
    assert "PRIVATE" not in json.dumps(result)


class ForbiddenBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("health error body must never be read")
        yield b""


@pytest.mark.asyncio
async def test_health_reads_status_only():
    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(503, stream=ForbiddenBody())
        return httpx.Response(403, json={"private_error": "PRIVATE"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await collect_snapshot(client, NOW)
    assert set(result["health"].values()) == {503}
    assert result["counters"]["value"] is None
    assert "PRIVATE" not in json.dumps(result)
