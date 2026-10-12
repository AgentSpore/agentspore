"""Real MCP schema/dispatch handlers with mock outward HTTP."""

import json
from unittest.mock import AsyncMock

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

TASK_ID = "12345678-1234-1234-1234-123456789012"
SUBMIT = {"result": "proof", "expected_version": 1, "idempotency_key": TASK_ID}
CREATE = {
    "title": "Docs",
    "description": "Acceptance criteria",
    "idempotency_key": TASK_ID,
    "review_required": True,
    "learning_credit_amount": 30,
}
OPERATIONS = [
    (
        "create_task",
        {"project_id": TASK_ID, "payload": CREATE},
        "POST",
        f"/projects/{TASK_ID}/tasks",
        CREATE,
    ),
    ("list_tasks", {"filters": {"limit": 2}}, "GET", "/tasks", None),
    ("claim_task", {"task_id": TASK_ID}, "POST", f"/tasks/{TASK_ID}/claim", None),
    ("unclaim_task", {"task_id": TASK_ID}, "POST", f"/tasks/{TASK_ID}/unclaim", None),
    (
        "submit_task",
        {"task_id": TASK_ID, "payload": SUBMIT},
        "POST",
        f"/tasks/{TASK_ID}/submit",
        SUBMIT,
    ),
    (
        "review_task",
        {
            "task_id": TASK_ID,
            "payload": {
                "action": "accept",
                "expected_version": 2,
                "idempotency_key": TASK_ID,
            },
        },
        "POST",
        f"/tasks/{TASK_ID}/review",
        {"action": "accept", "expected_version": 2, "idempotency_key": TASK_ID},
    ),
    ("get_task_review", {"task_id": TASK_ID}, "GET", f"/tasks/{TASK_ID}/review", None),
    ("get_learning_budget", {}, "GET", "/learning-budget", None),
    ("activate_learning_budget", {}, "POST", "/learning-budget", None),
]


@pytest_asyncio.fixture
async def bridge():
    instance = EventBridge("af_synthetic", "https://example.test", 4)
    await instance._http.aclose()
    yield instance
    await instance.close()


async def invoke(server, name, arguments):
    """Invoke the real registered MCP request handler."""
    result = await server.request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    return result.root


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status", [200, 401, 403, 404, 409, 422, 500])
async def test_real_mcp_handler(bridge, operation, status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"receipt": True} if status == 200 else {"detail": bridge.api_key},
        )

    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url,
        headers={"X-API-Key": bridge.api_key},
        transport=httpx.MockTransport(respond),
    )
    operation, args, verb, path, body = operation
    result = await invoke(_make_server(bridge), "agentspore_" + operation, args)
    assert result.isError == (status != 200)
    data = json.loads(result.content[0].text)
    assert data == (
        {"receipt": True}
        if status == 200
        else {"error": "http_error", "status_code": status}
    )
    assert bridge.api_key not in result.model_dump_json()
    assert len(requests) == 1
    assert requests[0].method == verb
    assert requests[0].url.path == "/api/v1/agents" + path
    assert requests[0].headers["X-API-Key"] == bridge.api_key
    assert (json.loads(requests[0].content) if requests[0].content else None) == body


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError, ValueError])
async def test_safe_unknown_outcome(bridge, failure):
    requests = []

    def disconnect(request):
        requests.append(request)
        raise failure(bridge.api_key)

    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url, transport=httpx.MockTransport(disconnect)
    )
    result = await invoke(
        _make_server(bridge),
        "agentspore_submit_task",
        {"task_id": TASK_ID, "payload": SUBMIT},
    )
    assert result.isError
    assert json.loads(result.content[0].text)["outcome"] == "unknown"
    assert bridge.api_key not in result.model_dump_json()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {**SUBMIT, "expected_version": -1},
        {**SUBMIT, "extra": True},
        {**SUBMIT, "result": " "},
        {**SUBMIT, "idempotency_key": "bad"},
        {**SUBMIT, "expected_version": True},
    ],
)
async def test_invalid_submit_never_sends(bridge, payload):
    requests = []
    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        ),
    )
    result = await invoke(
        _make_server(bridge),
        "agentspore_submit_task",
        {"task_id": TASK_ID, "payload": payload},
    )
    assert result.isError
    assert not requests


@pytest.mark.parametrize(
    "payload",
    [
        {**CREATE, "learning_credit_amount": True},
        {**CREATE, "learning_credit_amount": 0},
        {**CREATE, "learning_credit_amount": 1000000001},
        {**CREATE, "review_required": False},
    ],
)
async def test_invalid_price_never_sends(bridge, payload):
    requests = []
    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        ),
    )
    result = await invoke(
        _make_server(bridge),
        "agentspore_create_task",
        {"project_id": TASK_ID, "payload": payload},
    )
    assert result.isError
    assert not requests


async def test_tool_list_and_legacy_ws_notice(bridge):
    server = _make_server(bridge)
    result = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list")
    )
    assert isinstance(result.root, ListToolsResult)
    tools = {tool.name: tool for tool in result.root.tools}
    assert len(tools) == 21
    description = tools["agentspore_task_complete"].description
    assert isinstance(description, str)
    assert "does not complete" in description
    bridge._ws = AsyncMock()
    bridge._connected.set()
    result = await invoke(server, "agentspore_task_complete", {"task_id": TASK_ID})
    assert not result.isError
    bridge._ws.send.assert_awaited_once_with(
        json.dumps({"type": "task_complete", "task_id": TASK_ID})
    )
