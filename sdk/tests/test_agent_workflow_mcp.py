"""Real MCP dispatch and authenticated HTTP boundary; no live platform writes."""

import json
from typing import Any

import httpx
import pytest
import pytest_asyncio
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    ListToolsRequest,
    ListToolsResult,
)

from agentspore_sdk.mcp_server import EventBridge, _make_server

ID = "12345678-1234-1234-1234-123456789012"
SHA = "a" * 40
SECRET = "synthetic-secret-not-for-output"
IDENTITY = {
    "agent_id": ID,
    "name": "Synthetic Agent",
    "handle": "synthetic",
    "is_active": True,
}
HEARTBEAT: dict[str, Any] = {
    key: []
    for key in (
        "tasks",
        "feedback",
        "notifications",
        "direct_messages",
        "memory_context",
        "agent_events",
        "warnings",
        "project_usage",
    )
}
HEARTBEAT["next_heartbeat_seconds"] = 14400
PROJECT = {
    "id": ID,
    "title": "Synthetic",
    "status": "active",
    "repo_url": "https://github.com/example/service",
    "creator_agent_id": ID,
}
STATUS = {
    "status": "failed",
    "reason_code": "health_failed",
    "active_revision": ID,
    "url": "https://synthetic.services.example",
    "deployments": [
        {
            "id": ID,
            "status": "failed",
            "reason_code": "health_failed",
            "commit_sha": SHA,
        }
    ],
}
OPERATIONS = [
    ("identity", {}, "GET", "/me", IDENTITY),
    ("heartbeat", {}, "POST", "/heartbeat", HEARTBEAT),
    ("list_projects", {}, "GET", "/projects", [PROJECT]),
    (
        "submit_deployment",
        {"project_id": ID, "commit_sha": SHA, "idempotency_key": ID},
        "POST",
        f"/projects/{ID}/deploy",
        {"deployment_id": ID, "status": "queued"},
    ),
    ("deployment_status", {"project_id": ID}, "GET", f"/projects/{ID}/deploy", STATUS),
]


@pytest_asyncio.fixture
async def bridge():
    instance = EventBridge("af_" + SECRET, "https://example.test", 4)
    await instance._http.aclose()
    yield instance
    await instance.close()


def transport(bridge, handler):
    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url,
        headers={"X-API-Key": bridge.api_key},
        transport=httpx.MockTransport(handler),
    )


async def invoke(bridge, operation, arguments):
    server = _make_server(bridge)
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="agentspore_" + operation, arguments=arguments
            ),
        )
    )
    return result.root


async def test_real_catalog_closed_schemas(bridge):
    server = _make_server(bridge)
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list")
    )
    assert isinstance(result.root, ListToolsResult)
    tools = {tool.name: tool for tool in result.root.tools}
    for operation, *_ in OPERATIONS:
        assert (
            tools["agentspore_" + operation].inputSchema["additionalProperties"]
            is False
        )
    assert "capacity zero" in (tools["agentspore_heartbeat"].description or "")
    assert (
        tools["agentspore_list_projects"].inputSchema["properties"]["mine"]["default"]
        is True
    )


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_success_exact_transport_and_projection(bridge, operation):
    name, args, method, path, response = operation
    requests = []

    def handle(request):
        requests.append(request)
        body = json.loads(json.dumps(response))
        for row in body if isinstance(body, list) else [body]:
            row.update(
                owner_email=SECRET,
                api_key=SECRET,
                github_login=SECRET,
                private_metadata={"token": SECRET},
            )
        if name == "heartbeat":
            body["direct_messages"] = [{"content": SECRET}]
        return httpx.Response(202 if name == "submit_deployment" else 200, json=body)

    transport(bridge, handle)
    result = await invoke(bridge, name, args)
    assert not result.isError
    assert SECRET not in result.model_dump_json()
    assert len(requests) == 1
    request = requests[0]
    assert request.method == method and request.url.path == "/api/v1/agents" + path
    assert request.headers["X-API-Key"] == bridge.api_key
    data = json.loads(result.content[0].text)
    if name == "heartbeat":
        assert json.loads(request.content) == {
            "status": "idle",
            "current_capacity": 0,
            "completed_tasks": [],
            "read_dm_ids": [],
            "read_notification_ids": [],
            "acked_event_ids": [],
            "available_for": [],
            "insights": [],
        }
        assert data["counts"]["tasks"] == 0 and data["counts"]["direct_messages"] == 1
        assert data["connectivity_only"] is True
    if name == "submit_deployment":
        assert request.headers["Idempotency-Key"] == ID
        assert json.loads(request.content) == {"commit_sha": SHA}
        assert "url" not in data
    if name == "list_projects":
        assert request.url.params["mine"] == "true"
        assert data["scope"] == "created_by_authenticated_agent"
    if name == "deployment_status":
        assert (
            data["status"] == "failed" and data["active_revision"] == ID and data["url"]
        )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, None),
        (403, "not_project_author"),
        (404, "project_not_found"),
        (409, "idempotency_conflict"),
        (422, None),
        (429, "agent_daily_quota"),
        (503, "deploy_disabled"),
        (500, None),
    ],
)
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_denials_safe_no_retry(bridge, operation, status, code):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"detail": {"reason_code": code or SECRET, "message": SECRET}},
            headers={"Retry-After": "45", "X-Secret": SECRET},
        )

    transport(bridge, handle)
    result = await invoke(bridge, operation[0], operation[1])
    assert (
        result.isError and SECRET not in result.model_dump_json() and len(requests) == 1
    )
    data = json.loads(result.content[0].text)
    assert data["status_code"] == status and data["automatic_retry"] is False
    assert data.get("reason_code") == code
    assert data["retry_after_seconds"] == 45


@pytest.mark.parametrize("operation", [OPERATIONS[1], OPERATIONS[3]])
@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError, ValueError])
async def test_uncertain_write_never_retried(bridge, operation, failure):
    requests = []

    def handle(request):
        requests.append(request)
        raise failure(SECRET)

    transport(bridge, handle)
    result = await invoke(bridge, operation[0], operation[1])
    data = json.loads(result.content[0].text)
    assert (
        result.isError
        and data["outcome"] == "unknown"
        and data["automatic_retry"] is False
    )
    assert len(requests) == 1 and SECRET not in result.model_dump_json()


@pytest.mark.parametrize(
    "operation,args",
    [
        ("identity", {"extra": True}),
        ("heartbeat", {"current_capacity": 1}),
        ("heartbeat", {"status": "unknown"}),
        ("list_projects", {"limit": 101}),
        ("list_projects", {"mine": "true"}),
        ("deployment_status", {"project_id": "../me"}),
        (
            "submit_deployment",
            {"project_id": ID, "commit_sha": "main", "idempotency_key": ID},
        ),
        ("submit_deployment", {"project_id": ID, "commit_sha": SHA}),
    ],
)
async def test_invalid_schema_prevents_http(bridge, operation, args):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={})

    transport(bridge, handle)
    result = await invoke(bridge, operation, args)
    assert result.isError and requests == []


async def test_public_catalog_not_auth_proof(bridge):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[PROJECT])

    transport(bridge, handle)
    data = json.loads(
        (await invoke(bridge, "list_projects", {"mine": False})).content[0].text
    )
    assert (
        data["authenticated"] is False
        and data["creator_filter_applied"] is False
        and data["scope"] == "public_catalog"
    )
    assert requests[0].url.params["mine"] == "false"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.test/path",
        "https://example.test/?token=secret",
        "https://example.test/#secret",
        "http://example.test",
        "https://example.test\\@evil.test",
    ],
)
async def test_unsafe_urls_never_returned(bridge, url):
    transport(bridge, lambda request: httpx.Response(200, json={**STATUS, "url": url}))
    result = await invoke(bridge, "deployment_status", {"project_id": ID})
    assert json.loads(result.content[0].text)["url"] is None


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_malformed_success_fails_closed(bridge, operation):
    transport(bridge, lambda request: httpx.Response(200, json={"secret": SECRET}))
    result = await invoke(bridge, operation[0], operation[1])
    assert result.isError and SECRET not in result.model_dump_json()
    if operation[2] == "POST":
        assert json.loads(result.content[0].text)["outcome"] == "unknown"


async def test_caller_controlled_idempotency_reconciliation(bridge):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout(SECRET)
        return httpx.Response(202, json={"deployment_id": ID, "status": "building"})

    transport(bridge, handle)
    args = {"project_id": ID, "commit_sha": SHA.upper(), "idempotency_key": ID}
    first = await invoke(bridge, "submit_deployment", args)
    assert first.isError and len(requests) == 1
    second = await invoke(bridge, "submit_deployment", args)
    assert not second.isError and len(requests) == 2
    assert (
        requests[0].content
        == requests[1].content
        == json.dumps({"commit_sha": SHA.upper()}, separators=(",", ":")).encode()
    )
    assert (
        requests[0].headers["Idempotency-Key"]
        == requests[1].headers["Idempotency-Key"]
        == ID
    )


@pytest.mark.parametrize(
    "retry", [SECRET, "999999999999999999999", "-1", "Wed, 21 Oct 2015 07:28:00 GMT"]
)
async def test_untrusted_retry_header_not_echoed(bridge, retry):
    transport(
        bridge,
        lambda request: httpx.Response(
            429, json={"detail": SECRET}, headers={"Retry-After": retry}
        ),
    )
    result = await invoke(bridge, "identity", {})
    data = json.loads(result.content[0].text)
    assert "retry_after_seconds" not in data and SECRET not in result.model_dump_json()


async def test_redirect_never_forwards_authenticated_request(bridge):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/secret"})

    transport(bridge, handle)
    bridge._http.follow_redirects = True
    result = await invoke(bridge, "identity", {})
    assert result.isError and len(requests) == 1
    assert "other.example" not in result.model_dump_json()


@pytest.mark.parametrize("mine", [True, False])
async def test_empty_projects_prove_no_general_write_authority(bridge, mine):
    transport(bridge, lambda request: httpx.Response(200, json=[]))
    result = await invoke(bridge, "list_projects", {"mine": mine})
    assert not result.isError
    data = json.loads(result.content[0].text)
    assert data["projects"] == []
    assert data["authenticated"] is mine
    assert data["creator_filter_applied"] is mine
    assert "creator_authorization_proven" not in data
    assert "can_deploy" not in data
