"""Exercise prototype publication against the current API wire contracts."""

import json
from uuid import UUID

import httpx
import pytest

from agentspore_sdk import operator_growth_publish as publish_module
from agentspore_sdk.operator_growth_publish import GrowthPublisher

AGENT = "00000000-0000-4000-8000-000000000001"
PROJECT = "00000000-0000-4000-8000-000000000002"
TITLE = "peopletool-" + "1" * 32
REPO = "https://github.com/AgentSpore/" + TITLE
SHA = "a" * 40


@pytest.fixture
def service():
    return {
        "title": "PeopleTool",
        "description": "A small browser tool prototype.",
        "source_ids": ["demand.1", "demand.2"],
        "files": [
            {"path": "index.html", "content": "<!doctype html><title>Tool</title>"},
            {"path": "README.md", "content": "Prototype, not deployed."},
            {
                "path": "tests/tool.test.js",
                "content": "require('node:test')('test',()=>{});",
            },
        ],
    }


@pytest.fixture
def publication(monkeypatch):
    monkeypatch.setattr(publish_module, "uuid4", lambda: UUID(hex="1" * 32))
    calls = []
    project = {
        "id": PROJECT,
        "title": TITLE,
        "creator_agent_id": AGENT,
        "repo_url": REPO,
        "warning": None,
    }
    scenario = {
        "catalog": [],
        "profile": {"agent_id": AGENT, "name": "Platform Builder"},
        "create": project,
        "detail": dict(project),
        "push": {
            "status_code": 200,
            "data": {"sha": SHA, "files_changed": 3, "branch": "main"},
        },
    }

    def respond(request):
        calls.append(request)
        if request.url.host == "api.github.com":
            return scenario.get("github", httpx.Response(404))
        path = request.url.path
        key = (
            "catalog"
            if path == "/api/v1/projects"
            else (
                "profile"
                if path.endswith("/me")
                else "push"
                if path.endswith("/github")
                else "create"
                if request.method == "POST"
                else "detail"
            )
        )
        value = scenario[key]
        if isinstance(value, Exception):
            raise value
        return (
            value
            if isinstance(value, httpx.Response)
            else httpx.Response(200, json=value)
        )

    return httpx.MockTransport(respond), calls, scenario


async def test_exact_creation_and_atomic_push_do_not_inherit_credentials(
    publication, service
):
    transport, calls, _ = publication
    async with httpx.AsyncClient(
        transport=transport,
        auth=("synthetic", "password"),
        cookies={"session": "synthetic-cookie"},
        headers={"X-Extra": "ignored"},
        base_url="https://untrusted.invalid",
        follow_redirects=True,
    ) as client:
        publisher = GrowthPublisher(client, "af_synthetic")
        assert await publisher.create(service) == {
            "project_id": PROJECT,
            "repo_url": REPO,
            "creator_agent_id": AGENT,
            "project_title": TITLE,
        }
        assert await publisher.push(service) == SHA
    assert [(r.method, r.url.path) for r in calls] == [
        ("GET", "/api/v1/agents/me"),
        ("GET", "/api/v1/projects"),
        ("GET", "/repos/AgentSpore/" + TITLE),
        ("POST", "/api/v1/agents/projects"),
        ("GET", "/api/v1/projects/" + PROJECT),
        ("POST", "/api/v1/agents/projects/" + PROJECT + "/github"),
    ]
    for request in calls:
        if request.url.host == "api.github.com":
            assert "x-api-key" not in request.headers
        else:
            assert request.url.host == "agentspore.com"
            assert request.headers["X-API-Key"] == "af_synthetic"
        assert not {"authorization", "cookie", "x-extra"} & set(request.headers)
    assert json.loads(calls[-1].content) == {
        "method": "PUT",
        "path": "/contents",
        "body": {
            "files": service["files"],
            "message": "feat: add PeopleTool prototype",
            "branch": "main",
        },
    }


@pytest.mark.parametrize(
    "body",
    [
        {"status_code": 403, "data": {"sha": SHA}},
        {
            "status_code": 200,
            "data": {"sha": SHA, "files_changed": 0, "branch": "main"},
        },
        {
            "status_code": 200,
            "data": {"sha": "bad", "files_changed": 3, "branch": "main"},
        },
        {
            "status_code": 200,
            "data": {"sha": SHA, "files_changed": 3, "branch": "other"},
        },
        {
            "status_code": 200,
            "data": {"sha": SHA, "files_changed": True, "branch": "main"},
        },
    ],
)
async def test_nested_failure_never_becomes_success(publication, service, body):
    transport, _, scenario = publication
    scenario["push"] = body
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "af_synthetic")
        assert await publisher.create(service)
        assert await publisher.push(service) is None
        with pytest.raises(ValueError, match="already_attempted"):
            await publisher.push(service)


@pytest.mark.parametrize(
    "field,value",
    [
        ("creator_agent_id", PROJECT),
        ("id", "not-uuid"),
        ("repo_url", "https://github.com/Other/PeopleTool"),
        ("repo_url", "https://github.com/AgentSpore/tool?token=synthetic"),
        ("warning", "vcs_unavailable"),
        ("title", "OtherTool"),
    ],
)
async def test_unverified_creation_prevents_push(publication, service, field, value):
    transport, calls, scenario = publication
    scenario["create"][field] = value
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "af_synthetic")
        assert await publisher.create(service) is None
        with pytest.raises(ValueError, match="mismatch"):
            await publisher.push(service)
        with pytest.raises(ValueError, match="already_attempted"):
            await publisher.create(service)
    assert len(calls) == 4


async def test_ownership_change_before_push_prevents_write(publication, service):
    transport, calls, scenario = publication
    scenario["detail"]["creator_agent_id"] = PROJECT
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "af_synthetic")
        assert await publisher.create(service)
        assert await publisher.push(service) is None
    assert len(calls) == 5


async def test_service_changed_after_creation_prevents_push(publication, service):
    transport, calls, _ = publication
    async with httpx.AsyncClient(transport=transport) as client:
        publisher = GrowthPublisher(client, "af_synthetic")
        assert await publisher.create(service)
        service["files"][0]["content"] += "changed"
        with pytest.raises(ValueError, match="mismatch"):
            await publisher.push(service)
    assert len(calls) == 4


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://untrusted.invalid"}),
        httpx.Response(200, content=b"x" * 65537),
        httpx.Response(200, content=b'{"agent_id":"x","agent_id":"y"}'),
        httpx.ReadTimeout("synthetic timeout"),
        httpx.Response(429),
        httpx.Response(200, json={"agent_id": "bad", "name": "Builder"}),
    ],
)
async def test_unavailable_identity_never_writes(publication, service, response):
    transport, calls, scenario = publication
    scenario["profile"] = response
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        assert await GrowthPublisher(client, "af_synthetic").create(service) is None
    assert len(calls) == 1 and calls[0].method == "GET"


async def test_invalid_manifest_never_reads_or_writes(publication, service):
    transport, calls, _ = publication
    service["files"][0]["delete"] = True
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError):
            await GrowthPublisher(client, "af_synthetic").create(service)
    assert calls == []


@pytest.mark.parametrize(
    "catalog",
    [[{"title": " peopletool "}], None, [{"title": 4}], [{"title": "Other"}] * 100],
)
async def test_catalog_duplicate_unavailable_or_incomplete_prevents_write(
    publication, service, catalog
):
    transport, calls, scenario = publication
    scenario["catalog"] = catalog
    async with httpx.AsyncClient(transport=transport) as client:
        assert await GrowthPublisher(client, "af_synthetic").create(service) is None
    assert all(request.method == "GET" for request in calls)
    assert len(calls) <= 6


@pytest.mark.parametrize("status", [200, 301, 403, 429, 503])
async def test_existing_or_unknown_github_repository_prevents_creation(
    publication, service, status
):
    transport, calls, scenario = publication
    scenario["github"] = httpx.Response(
        status, headers={"location": "https://untrusted.invalid"}
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        assert await GrowthPublisher(client, "af_synthetic").create(service) is None
    assert all(request.method == "GET" for request in calls)
    assert calls[-1].url.path == "/repos/AgentSpore/" + TITLE
