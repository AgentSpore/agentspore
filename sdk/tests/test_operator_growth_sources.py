"""Exercise public growth collection at the real HTTP transport boundary."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from agentspore_sdk.operator_digest import canonical
from agentspore_sdk.operator_growth_sources import collect_growth

NOW = datetime(2026, 10, 23, tzinfo=UTC)
IDENTITY = "159aaae9-1627-594b-bf8c-4b24b46f2191"


@pytest.fixture
def source_payloads():
    # Moltbook envelope/field names were verified against the public jobs feed.
    return {
        "agentspore.com": [
            {
                "id": IDENTITY,
                "title": "Family app",
                "description": "A useful app",
                "repo_url": "https://github.com/AgentSpore/app",
                "email": "PRIVATE",
                "deploy_url": "http://127.0.0.1",
            }
        ],
        "api.github.com": {
            "items": [
                {
                    "id": 42,
                    "title": "Looking for an app",
                    "body": "Please build this",
                    "html_url": "https://github.com/example/tool/issues/7",
                    "user": {"email": "PRIVATE"},
                }
            ]
        },
        "www.moltbook.com": {
            "success": True,
            "posts": [
                {
                    "id": IDENTITY,
                    "title": "Available for projects",
                    "content": "I build apps",
                    "author": {"email": "PRIVATE"},
                }
            ],
            "has_more": True,
        },
    }


@pytest.mark.asyncio
async def test_public_sources_isolate_credentials_and_project_fields(source_payloads):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=source_payloads[request.url.host])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        auth=("secret", "secret"),
        headers={"X-API-Key": "PRIVATE", "Authorization": "PRIVATE"},
        cookies={"session": "PRIVATE"},
        follow_redirects=True,
    ) as client:
        bundle = await collect_growth(client, NOW)
    assert len(requests) == 4
    assert all(r.method == "GET" for r in requests)
    assert all(
        not any(h in r.headers for h in ("authorization", "x-api-key", "cookie"))
        for r in requests
    )
    assert all(r.extensions["timeout"]["read"] == 10 for r in requests)
    assert "PRIVATE" not in json.dumps(bundle)
    assert "127.0.0.1" not in json.dumps(bundle)
    assert set(bundle["coverage"]["source_kinds"].values()) == {
        "project",
        "demand",
        "agent",
    }
    assert bundle["coverage"]["project_titles"] == ["Family app"]
    assert "unverified" in bundle["facts"][f"agent.{IDENTITY}"]
    digest = bundle.pop("snapshot_digest")
    assert digest == hashlib.sha256(canonical(bundle)).hexdigest()
    assert bundle["purpose"] == "growth"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["redirect", "rate_limit", "oversize", "malformed", "shape", "network", "timeout"],
)
async def test_sources_fail_unavailable_without_following_redirect(failure):
    requests = []

    def respond(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "network":
            raise httpx.ConnectError("unavailable", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        if failure == "rate_limit":
            return httpx.Response(429)
        if failure == "oversize":
            return httpx.Response(200, content=b" " * 1_048_577)
        if failure == "malformed":
            return httpx.Response(200, content=b"{")
        return httpx.Response(200, json={"unexpected": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), follow_redirects=True
    ) as client:
        bundle = await collect_growth(client, NOW)
    assert len(requests) == 4
    assert bundle["facts"] == {}
    assert all(
        bundle["coverage"][key]["status"] == "unavailable"
        for key in ("projects", "demand_search_1", "demand_search_2", "agent_jobs")
    )
    assert all(r.url.host != "169.254.169.254" for r in requests)


@pytest.mark.asyncio
async def test_bounded_output_rejects_invalid_links_and_skips_pii(source_payloads):
    source_payloads["agentspore.com"] *= 30
    row = source_payloads["api.github.com"]["items"][0]
    source_payloads["api.github.com"]["items"] = [
        dict(row, id=i + 1, body="世" * 5000) for i in range(30)
    ]
    source_payloads["api.github.com"]["items"][0]["html_url"] = (
        "https://github.com@evil.test/x/y/issues/1"
    )
    source_payloads["www.moltbook.com"]["posts"][0]["content"] = "世" * 5000

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=source_payloads[r.url.host])
        )
    ) as client:
        bundle = await collect_growth(client, NOW)
    assert len(canonical(bundle)) <= 50 * 1024
    assert len(bundle["facts"]) <= 60
    assert all(len(value) <= 1600 for value in bundle["facts"].values())
    assert (
        set(bundle["facts"])
        == set(bundle["coverage"]["source_urls"])
        == set(bundle["coverage"]["source_kinds"])
    )
    assert "evil.test" not in json.dumps(bundle)
    assert bundle["coverage"]["demand_search_1"]["status"] == "partial"


@pytest.mark.asyncio
async def test_unicode_budget_keeps_fact_maps_consistent(source_payloads):
    project = source_payloads["agentspore.com"][0]
    post = source_payloads["www.moltbook.com"]["posts"][0]
    source_payloads["agentspore.com"] = [
        dict(project, id=str(UUID(int=i + 1)), title="世" * 120, description="世" * 500)
        for i in range(20)
    ]
    source_payloads["www.moltbook.com"]["posts"] = [
        dict(post, id=str(UUID(int=i + 1)), title="世" * 120, content="世" * 500)
        for i in range(10)
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=source_payloads[r.url.host])
        )
    ) as client:
        bundle = await collect_growth(client, NOW)
    assert len(canonical(bundle)) <= 50 * 1024
    assert bundle["coverage"]["output"] == {
        "status": "partial",
        "reason": "output_budget",
    }
    assert (
        set(bundle["facts"])
        == set(bundle["coverage"]["source_urls"])
        == set(bundle["coverage"]["source_kinds"])
    )
