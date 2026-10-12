"""Verify reviewed REST requests without retrying state changes."""

import json

import httpx
import pytest
import pytest_asyncio

from agentspore_sdk import AgentClient

TASK_ID = "12345678-1234-1234-1234-123456789012"
PAYLOAD = {"result": "proof", "expected_version": 1, "idempotency_key": TASK_ID}
REVIEW = {"action": "accept", "expected_version": 1, "idempotency_key": TASK_ID}
CREATE = {
    "title": "Docs",
    "description": "Acceptance criteria",
    "idempotency_key": TASK_ID,
    "review_required": True,
    "learning_credit_amount": 30,
}
OPERATIONS = [
    ("create_task", (TASK_ID, CREATE), "POST", f"/projects/{TASK_ID}/tasks", CREATE),
    ("list_tasks", ({"type": "write_docs", "limit": 2},), "GET", "/tasks", None),
    ("claim_task", (TASK_ID,), "POST", f"/tasks/{TASK_ID}/claim", None),
    ("unclaim_task", (TASK_ID,), "POST", f"/tasks/{TASK_ID}/unclaim", None),
    ("submit_task", (TASK_ID, PAYLOAD), "POST", f"/tasks/{TASK_ID}/submit", PAYLOAD),
    (
        "review_task",
        (TASK_ID, REVIEW),
        "POST",
        f"/tasks/{TASK_ID}/review",
        REVIEW,
    ),
    ("get_task_review", (TASK_ID,), "GET", f"/tasks/{TASK_ID}/review", None),
    ("get_learning_budget", (), "GET", "/learning-budget", None),
    ("activate_learning_budget", (), "POST", "/learning-budget", None),
]


@pytest_asyncio.fixture
async def client():
    instance = AgentClient("af_synthetic", "https://example.test")
    await instance._http.aclose()
    yield instance
    await instance.stop()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status", [200, 401, 403, 404, 409, 422, 500])
async def test_wire_contract(client, operation, status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"status": "receipt"})

    client._http = httpx.AsyncClient(
        base_url=client.base_url,
        headers={"X-API-Key": client.api_key},
        transport=httpx.MockTransport(respond),
    )
    method, args, verb, path, body = operation
    if status == 200:
        assert await getattr(client.marketplace, method)(*args) == {"status": "receipt"}
    else:
        with pytest.raises(httpx.HTTPStatusError) as error:
            await getattr(client.marketplace, method)(*args)
        assert error.value.response.status_code == status
    assert len(requests) == 1
    request = requests[0]
    assert request.method == verb
    assert request.url.path == "/api/v1/agents" + path
    assert request.headers["X-API-Key"] == client.api_key
    assert (json.loads(request.content) if request.content else None) == body
    if method == "list_tasks":
        assert dict(request.url.params) == {"type": "write_docs", "limit": "2"}


@pytest.mark.parametrize("operation", OPERATIONS)
async def test_unknown_outcome_no_retry(client, operation):
    requests = []

    def disconnect(request):
        requests.append(request)
        raise httpx.ReadTimeout("Synthetic timeout", request=request)

    client._http = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(disconnect)
    )
    method, args, *_ = operation
    with pytest.raises(httpx.ReadTimeout):
        await getattr(client.marketplace, method)(*args)
    assert len(requests) == 1


async def test_caller_controlled_replay_preserves_payload(client):
    requests = []
    receipt = {"task_id": TASK_ID, "status": "open", "created": False}
    client._http = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request) or httpx.Response(200, json=receipt)
            )
        ),
    )
    payload = {**CREATE, "learning_credit_amount": None}
    original = dict(payload)
    assert await client.marketplace.create_task(TASK_ID, payload) == receipt
    assert await client.marketplace.create_task(TASK_ID, payload) == receipt
    assert payload == original
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert json.loads(requests[0].content) == original
