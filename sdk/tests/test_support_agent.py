"""Exercise real delivery state against mocked external HTTP boundaries."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from agentspore_sdk import AgentClient
from agentspore_sdk.support_agent import SupportAgent

DM = "12345678-1234-4234-8234-123456789012"
EVENT = "12345678-1234-4234-8234-123456789013"


@pytest_asyncio.fixture
async def support(tmp_path):
    calls = []
    scenario = {"failure": "", "price": "0"}

    def respond(request):
        calls.append(request)
        path = request.url.path
        public = {
            "/skill.md": httpx.Response(200, text="Register an external agent; save its key privately."),
            "/api/v1/agents/stats": httpx.Response(200, json={"total_agents": 1}),
            "/api/v1/agents/projects": httpx.Response(200, json=[{"id": "public", "title": "Catalog"}]),
            "/api/v1/agents/me": httpx.Response(200, json={"name": "AdminAgentSpore"}),
        }
        if path in public:
            return public[path]
        if path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": SupportAgent.MODEL, "pricing": {"prompt": scenario["price"], "completion": "0"}}]
                },
            )
        if path.endswith("/heartbeat"):
            return httpx.Response(200, json={"direct_messages": [], "agent_events": []})
        if path.endswith("/completions"):
            if scenario["failure"] == "provider":
                raise httpx.ReadTimeout("synthetic", request=request)
            body = {"choices": [{"message": {"content": "Use POST /api/v1/agents/register with owner_email."}}]}
            return httpx.Response(200, content=json.dumps(scenario.get("provider_body", body)))
        if scenario["failure"] == "delivery":
            raise httpx.ReadTimeout("synthetic", request=request)
        return httpx.Response(200, json={"status": "ok"})

    client = AgentClient("af_synthetic", "https://example.test")
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url=client.base_url, headers={"X-API-Key": "af_synthetic"}, transport=httpx.MockTransport(respond)
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    application = SupportAgent(client, http, tmp_path / "requests.json", "provider-synthetic")
    yield application, calls, scenario
    await client.stop()
    await http.aclose()


async def test_direct_reply_deduplicates_concurrent_delivery_and_restart(support):
    app, calls, _ = support
    event = {"id": DM, "from": "human", "content": "Explain public project review attribution.", "event_id": EVENT}
    async with asyncio.TaskGroup() as group:
        for _ in range(3):
            group.create_task(app.reply(event))
    restored = SupportAgent(app.client, app.http, app.path, app.provider_key)
    await restored.reply(event)
    replies = [r for r in calls if r.url.path.endswith("/dm/reply")]
    models = [r for r in calls if r.url.path.endswith("/completions")]
    assert len(replies) == len(models) == 1
    assert json.loads(models[0].content)["reasoning"] == {"enabled": False}
    assert json.loads(replies[0].content)["reply_to_dm_id"] == DM
    assert "owner_email" in json.loads(replies[0].content)["content"]
    assert replies[0].headers["X-API-Key"] == "af_synthetic"
    assert app.requests[DM] == "sent"
    assert app.provider_key not in app.path.read_text()
    assert app.path.stat().st_mode & 0o777 == 0o600
    assert all("authorization" not in r.headers for r in calls if not r.url.path.endswith("/completions"))


@pytest.mark.parametrize("failure,status", [("provider", "provider_uncertain"), ("delivery", "delivery_uncertain")])
async def test_unknown_post_outcome_is_durable_and_never_retried(support, failure, status):
    app, calls, scenario = support
    scenario["failure"] = failure
    event = {"id": DM, "from": "human", "content": "Explain public project review attribution."}
    await app.reply(event)
    count = len(calls)
    restored = SupportAgent(app.client, app.http, app.path, app.provider_key)
    await restored.reply(event)
    assert len(calls) == count
    assert restored.requests[DM] == status


@pytest.mark.parametrize("price", ["0.001", "-1", "NaN", "invalid"])
async def test_pricing_guard_blocks_every_post(support, price):
    app, calls, scenario = support
    scenario["price"] = price
    await app.reply({"id": DM, "from": "human", "content": "Help"})
    assert not [r for r in calls if r.method == "POST"]
    assert app.requests[DM] == "blocked"


async def test_only_explicit_agent_requests_and_separate_ack_namespaces(support):
    app, calls, _ = support
    event = {"id": DM, "from": "smoke", "from_id": "agent", "content": "Welcome!"}
    await app.reply(event)
    assert not calls
    await app.reply({**event, "content": "/support Explain public project review attribution.", "event_id": EVENT})
    await app.synchronize()
    body = json.loads(calls[-1].content)
    assert body["read_dm_ids"] == [DM]
    assert body["acked_event_ids"] == [EVENT]
    assert body["current_capacity"] == 0 and body["available_for"] == []


async def test_secret_question_never_reaches_network_or_state(support):
    app, calls, _ = support
    secret = "af_" + "synthetic" * 4
    await app.reply({"id": DM, "from": "human", "content": secret})
    assert not calls and secret not in app.path.read_text()


@pytest.mark.parametrize(
    "body,status",
    [
        (None, "provider_invalid_response"),
        ([], "provider_invalid_response"),
        ({"choices": []}, "provider_invalid_response"),
        ({"choices": None}, "provider_invalid_response"),
        ({"choices": [None]}, "provider_invalid_response"),
        ({"choices": [{"message": None}]}, "provider_invalid_response"),
        ({"choices": [{"message": {"content": None}}]}, "provider_invalid_response"),
        (
            {"choices": [{"message": {"content": ["synthetic-provider-body"]}}]},
            "provider_invalid_response",
        ),
        ({"choices": [{"message": {"content": ""}}]}, "provider_empty_response"),
        ({"choices": [{"message": {"content": " \n "}}]}, "provider_empty_response"),
    ],
)
async def test_dispatch_reports_unusable_provider_response_without_retry(
    support, monkeypatch, body, status
):
    app, calls, scenario = support
    scenario["provider_body"] = body
    events = []
    monkeypatch.setattr(app, "_report_status", events.append)
    monkeypatch.setattr("agentspore_sdk.support_agent.transport_logger.disabled", True)
    app.client.on("dm")(app.reply)
    event = {
        "type": "dm",
        "id": DM,
        "from_id": "synthetic-agent",
        "content": "/support Explain public project review attribution.",
    }
    await app.client._dispatch(event)
    count = len(calls)
    restored = SupportAgent(app.client, app.http, app.path, app.provider_key)
    await restored.reply(event)
    assert len(calls) == count
    assert sum(request.url.path.endswith("/completions") for request in calls) == 1
    assert not any(request.url.path.endswith("/dm/reply") for request in calls)
    assert events == ["provider_uncertain", status, "ValueError"]
    assert restored.requests[DM] == "provider_uncertain"


async def test_supervision_cancels_children_and_closes_transports(support, monkeypatch):
    app, _, _ = support
    started, hold = asyncio.Event(), asyncio.Event()

    async def pending_socket():
        started.set()
        await hold.wait()

    monkeypatch.setattr(app.client, "start", pending_socket)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    task = asyncio.create_task(app.run())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.client._http.is_closed and app.http.is_closed


@pytest.mark.parametrize("question,russian", [
    ("How do I register and send heartbeat? Where is skill.md? Are hosted agents available?", False),
    ("Where is the onboarding skill?", False),
    ("Как зарегистрировать агента и получить skill.md?", True),
    ("Как отправить хартбит?", True),
    ("Есть ли управляемые агенты?", True),
])
async def test_onboarding_dispatch_uses_canonical_faq_not_false_model_answer(support, question, russian):
    app, calls, scenario = support
    scenario["provider_body"] = {"choices": [{"message": {"content": "Use GET /api/v1/agents/skill. Hosted agents ARE available."}}]}
    app.client.on("dm")(app.reply)
    await app.client._dispatch({"type": "dm", "id": DM, "from_id": "synthetic-agent", "content": "/support " + question})
    replies = [request for request in calls if request.url.path.endswith("/dm/reply")]
    assert len(replies) == 1
    answer = json.loads(replies[0].content)["content"]
    assert "GET https://agentspore.com/skill.md" in answer
    assert "POST https://agentspore.com/api/v1/agents/register" in answer
    assert "POST https://agentspore.com/api/v1/agents/heartbeat" in answer
    assert ("недоступны" if russian else "unavailable") in answer
    assert "/api/v1/agents/skill" not in answer
    assert not any(request.url.host == "openrouter.ai" for request in calls)
    assert app.requests[DM] == "sent"


def test_canonical_skill_documents_only_external_agent_onboarding():
    document = (Path(__file__).resolve().parents[2] / "skill.md").read_text()
    quick_reference = document.split("## What is AgentSpore?", 1)[0]
    for contract in ("GET /skill.md", "POST /api/v1/agents/register", "POST /api/v1/agents/heartbeat", "Hosted agents are unavailable"):
        assert contract in quick_reference
    assert "Hosted Agent Self-Management" not in document
    assert "/api/v1/hosted-agents/self" not in document
    assert "auto-injected" not in document
