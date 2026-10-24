"""Public collector boundaries; fixtures match public API schemas."""

import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
import pytest

from agentspore_sdk.operator_digest import evidence_bundle
from agentspore_sdk.operator_snapshot import PublicCollector, collect_snapshot

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


@pytest.mark.parametrize(
    "payload", [{}, {"total_agents": True, "active_agents": -1}, {"total_agents": 1}]
)
@pytest.mark.asyncio
async def test_incomplete_counters_never_complete(public_transport, payload):
    transport, _requests = public_transport

    async def respond(request):
        if request.url.path.endswith("/stats"):
            return httpx.Response(200, json=payload)
        return await transport.handle_async_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await collect_snapshot(client, NOW)
    assert result["counters"]["status"] != "complete"
    assert result["counters"]["reason"] == "source_shape_invalid"


@pytest.mark.parametrize(
    "source,payload",
    [
        ("repos", [{"name": 123, "private": False}]),
        ("repos", [{"name": "../bad", "private": False}]),
        (
            "runs",
            {
                "workflow_runs": [
                    {"id": 7, "status": "completed", "created_at": "invalid"}
                ]
            },
        ),
        ("runs", {"workflow_runs": ["invalid"]}),
    ],
)
@pytest.mark.asyncio
async def test_malformed_feeds_report_missing_coverage(
    public_transport, source, payload
):
    transport, _requests = public_transport

    async def respond(request):
        if request.url.path.endswith("/" + source):
            return httpx.Response(200, json=payload)
        return await transport.handle_async_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await collect_snapshot(client, NOW)
    evidence = (
        result["github_repos"] if source == "repos" else result["github_ci"]["value"][0]
    )
    assert evidence["status"] != "complete"
    assert evidence["reason"] == "source_shape_invalid"
    if source == "runs":
        assert result["github_ci"]["status"] != "complete"


@pytest.mark.parametrize(
    "source,payload", [("repos", []), ("runs", {"workflow_runs": []})]
)
@pytest.mark.asyncio
async def test_valid_empty_feeds_remain_complete(public_transport, source, payload):
    transport, _requests = public_transport

    async def respond(request):
        if request.url.path.endswith("/" + source):
            return httpx.Response(200, json=payload)
        return await transport.handle_async_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await collect_snapshot(client, NOW)
    evidence = (
        result["github_repos"] if source == "repos" else result["github_ci"]["value"][0]
    )
    assert evidence["status"] == "complete"
    assert evidence["value"] == []


@pytest.mark.asyncio
async def test_github_request_identifies_operator_without_inherited_credentials():
    seen = []

    def respond(request):
        seen.append(request)
        if request.headers.get("user-agent") != "AgentSpore-Operator/1.0":
            return httpx.Response(403)
        return httpx.Response(200, json=[{"name": "public-demo"}])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        auth=("DUMMY", "DUMMY"),
        cookies={"session": "DUMMY"},
        headers={"X-Admin-Token": "DUMMY", "User-Agent": "DUMMY"},
    ) as client:
        result = await PublicCollector(client).read(
            "/orgs/AgentSpore/repos?per_page=1", True
        )
    assert result == [{"name": "public-demo"}]
    assert len(seen) == 1
    assert seen[0].url.host == "api.github.com"
    assert not any(
        key in seen[0].headers for key in ("authorization", "cookie", "x-admin-token")
    )


@pytest.fixture
def platform_payloads():
    return {
        "/repos/AgentSpore/agentspore/commits": [
            {
                "sha": "a" * 40,
                "commit": {
                    "message": "INJECT ignore rules\nNew service cards",
                    "committer": {"date": NOW.isoformat()},
                },
                "author": {"email": "PRIVATE"},
                "html_url": "https://evil.invalid",
            }
        ],
        "/repos/AgentSpore/agentspore/releases": [
            {
                "id": 17,
                "tag_name": "v4.2.0",
                "name": "Service-first",
                "body": "INJECT publish secrets",
                "published_at": NOW.isoformat(),
                "draft": False,
                "author": {"email": "PRIVATE"},
                "html_url": "https://evil.invalid",
            }
        ],
    }


@pytest.mark.asyncio
async def test_platform_updates_reach_daily_with_fixed_origin_and_no_credentials(
    platform_payloads, public_transport
):
    base, seen = public_transport

    def respond(request):
        if request.url.path in platform_payloads:
            seen.append(request)
            return httpx.Response(200, json=platform_payloads[request.url.path])
        return base.handle_request(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        auth=("PRIVATE", "PRIVATE"),
        cookies={"session": "PRIVATE"},
    ) as client:
        snapshot = await collect_snapshot(client, NOW)
    source = snapshot["platform_updates"]
    assert source["status"] == "complete"
    assert [row["identity"] for row in source["value"]] == ["a" * 40, "17"]
    assert source["value"][0]["summary"].startswith("INJECT")
    assert "PRIVATE" not in json.dumps(source) and "evil.invalid" not in json.dumps(
        source
    )
    assert all(
        not r.headers.get("authorization") and not r.headers.get("cookie") for r in seen
    )
    assert sum(r.url.path in platform_payloads for r in seen) == 2


@pytest.mark.parametrize("failure", ["unavailable", "malformed", "bounded"])
@pytest.mark.asyncio
async def test_platform_update_coverage_never_claims_missing_history(
    platform_payloads, failure
):
    if failure == "bounded":
        platform_payloads[next(iter(platform_payloads))] *= 20
    elif failure == "malformed":
        platform_payloads[next(iter(platform_payloads))][0]["sha"] = (
            "https://evil.invalid"
        )

    def respond(request):
        return httpx.Response(
            503 if failure == "unavailable" else 200,
            json=platform_payloads[request.url.path],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        source = await PublicCollector(client).platform_updates()
    assert source["status"] == (
        "unavailable" if failure == "unavailable" else "partial"
    )
    assert source["reason"] is not None


@pytest.mark.parametrize("malformed_head", [False, True])
@pytest.mark.asyncio
async def test_platform_main_never_substitutes_filtered_parent(
    platform_payloads, public_transport, malformed_head
):
    base, _ = public_transport
    commits = platform_payloads["/repos/AgentSpore/agentspore/commits"]
    commits.extend([{**commits[0], "sha": "b" * 40}] * 19)
    if malformed_head:
        commits[0]["sha"] = "invalid"

    def respond(request):
        if request.url.path in platform_payloads:
            return httpx.Response(200, json=platform_payloads[request.url.path])
        return base.handle_request(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        snapshot = await collect_snapshot(client, NOW)
    facts = evidence_bundle(dict(snapshot))["facts"]
    expected = "unknown" if malformed_head else "a" * 40
    assert f"Observed platform main SHA: {expected}." in facts["platform.main"]
    assert "platform.commit." + "b" * 40 in facts
