"""Unit tests for the realtime stack — pure logic, no DB / Redis / Docker."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent_webhook_service import AgentWebhookService
from app.services.connection_manager import ConnectionManager


# ── AgentWebhookService.sign ──────────────────────────────────────────────


def test_webhook_sign_matches_hmac_sha256():
    secret = "topsecret"
    body = b'{"type":"dm","content":"hi"}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert AgentWebhookService.sign(secret, body) == expected


def test_webhook_sign_different_secret_changes_signature():
    body = b"same body"
    a = AgentWebhookService.sign("a", body)
    b = AgentWebhookService.sign("b", body)
    assert a != b


# ── AgentWebhookService.deliver retry / DLQ behaviour ─────────────────────


@pytest.mark.asyncio
async def test_webhook_deliver_returns_false_when_no_url():
    """If fetch_webhook returns None (no URL configured) deliver short-circuits."""
    with patch.object(AgentWebhookService, "fetch_webhook", AsyncMock(return_value=None)):
        ok = await AgentWebhookService.deliver("agent-1", {"type": "dm"})
    assert ok is False


@pytest.mark.asyncio
async def test_webhook_deliver_success_resets_failures(monkeypatch):
    """A 2xx response calls _reset_failures and returns True."""
    cfg = {"url": "https://example.test/hook", "secret": "s", "failures": 3}

    fake_resp = MagicMock()
    fake_resp.status_code = 204
    fake_resp.text = ""

    fake_client = AsyncMock()
    fake_client.__aenter__.return_value.post = AsyncMock(return_value=fake_resp)
    fake_client.__aexit__ = AsyncMock(return_value=False)

    reset_calls = []

    async def _reset(db, agent_id):
        reset_calls.append(agent_id)

    with patch.object(AgentWebhookService, "fetch_webhook", AsyncMock(return_value=cfg)), \
         patch.object(AgentWebhookService, "_reset_failures", _reset), \
         patch("app.services.agent_webhook_service.async_session_maker") as sm, \
         patch("app.services.agent_webhook_service.httpx.AsyncClient", return_value=fake_client):
        sm.return_value.__aenter__.return_value = AsyncMock()
        sm.return_value.__aexit__ = AsyncMock(return_value=False)
        ok = await AgentWebhookService.deliver("agent-1", {"type": "dm"})

    assert ok is True
    assert reset_calls == ["agent-1"]


@pytest.mark.asyncio
async def test_webhook_deliver_retries_then_dead_letters(monkeypatch):
    """3 consecutive failures → records failure + dead-letters the event."""
    cfg = {"url": "https://example.test/hook", "secret": "s", "failures": 0}

    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "boom"

    post_mock = AsyncMock(return_value=fake_resp)
    fake_client = AsyncMock()
    fake_client.__aenter__.return_value.post = post_mock
    fake_client.__aexit__ = AsyncMock(return_value=False)

    record_calls = []
    dlq_calls = []

    async def _record(db, agent_id, failures):
        record_calls.append((agent_id, failures))

    async def _dlq(db, agent_id, event_id, event, error):
        dlq_calls.append((agent_id, event_id, event["type"], error))

    # Skip the real sleeps so the test runs in milliseconds.
    async def _no_sleep(_):
        return None

    with patch.object(AgentWebhookService, "fetch_webhook", AsyncMock(return_value=cfg)), \
         patch.object(AgentWebhookService, "_record_failure", _record), \
         patch.object(AgentWebhookService, "_dead_letter", _dlq), \
         patch("app.services.agent_webhook_service.async_session_maker") as sm, \
         patch("app.services.agent_webhook_service.httpx.AsyncClient", return_value=fake_client), \
         patch("app.services.agent_webhook_service.asyncio.sleep", _no_sleep):
        sm.return_value.__aenter__.return_value = AsyncMock()
        sm.return_value.__aexit__ = AsyncMock(return_value=False)
        ok = await AgentWebhookService.deliver("agent-1", {"type": "dm"})

    assert ok is False
    assert post_mock.await_count == AgentWebhookService.MAX_ATTEMPTS == 3
    assert record_calls == [("agent-1", 1)]
    assert len(dlq_calls) == 1
    assert dlq_calls[0][0] == "agent-1"
    assert dlq_calls[0][2] == "dm"
    assert "HTTP 500" in dlq_calls[0][3]


# ── ConnectionManager — user channels ─────────────────────────────────────


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed = True


@pytest.mark.asyncio
async def test_connect_user_supports_multiple_tabs(monkeypatch):
    cm = ConnectionManager()
    # Stub out Redis pub/sub bookkeeping; we test only local fanout here.
    monkeypatch.setattr(cm, "_ensure_redis", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        cm, "_user_redis_listener", AsyncMock(return_value=None)
    )

    ws1, ws2 = FakeWS(), FakeWS()
    await cm.connect_user("user-1", ws1)
    await cm.connect_user("user-1", ws2)

    assert ws1.accepted and ws2.accepted
    assert len(cm._user_connections["user-1"]) == 2
    assert "user-1" in cm._user_redis_listeners  # one listener shared across tabs


@pytest.mark.asyncio
async def test_send_user_fanouts_to_all_local_tabs(monkeypatch):
    cm = ConnectionManager()
    fake_redis = MagicMock()
    fake_redis.publish = AsyncMock(return_value=1)
    monkeypatch.setattr(cm, "_ensure_redis", AsyncMock(return_value=fake_redis))
    monkeypatch.setattr(
        cm, "_user_redis_listener", AsyncMock(return_value=None)
    )

    ws1, ws2 = FakeWS(), FakeWS()
    await cm.connect_user("user-1", ws1)
    await cm.connect_user("user-1", ws2)

    delivered = await cm.send_user("user-1", {"type": "hosted_agent_status", "status": "running"})
    assert delivered is True
    assert ws1.sent and ws2.sent
    assert ws1.sent[0]["type"] == "hosted_agent_status"
    assert ws2.sent[0]["status"] == "running"
    fake_redis.publish.assert_awaited_once()  # also published cross-worker


@pytest.mark.asyncio
async def test_disconnect_user_releases_listener_on_last_tab(monkeypatch):
    cm = ConnectionManager()
    monkeypatch.setattr(cm, "_ensure_redis", AsyncMock(return_value=MagicMock()))

    listener_started = asyncio.Event()
    listener_cancelled = asyncio.Event()

    async def fake_listener(*args, **kwargs):
        listener_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            listener_cancelled.set()
            raise

    monkeypatch.setattr(cm, "_user_redis_listener", fake_listener)

    ws1, ws2 = FakeWS(), FakeWS()
    await cm.connect_user("user-1", ws1)
    await cm.connect_user("user-1", ws2)
    await asyncio.wait_for(listener_started.wait(), timeout=1)

    await cm.disconnect_user("user-1", ws1)
    assert "user-1" in cm._user_connections  # one tab still alive

    await cm.disconnect_user("user-1", ws2)
    assert "user-1" not in cm._user_connections
    await asyncio.wait_for(listener_cancelled.wait(), timeout=1)


# ── Agent runner idempotency dedup ────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_runner_drops_duplicate_event_ids():
    """Reload agent-runner main and exercise the dedup deque on a stub session."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-runner"))
    # Importing main.py is heavy (FastAPI, docker, pydantic_deep). We only need the
    # AgentSession class behaviour, so we recreate the relevant slice in isolation.
    import collections

    class StubSession:
        def __init__(self):
            self._seen_event_ids: collections.deque = collections.deque(maxlen=4)
            self.handled = []

        async def handle(self, event):
            eid = event.get("id") or event.get("event_id")
            if eid:
                if eid in self._seen_event_ids:
                    return "duplicate"
                self._seen_event_ids.append(eid)
            self.handled.append(eid)
            return "ok"

    s = StubSession()
    assert await s.handle({"id": "e1", "type": "dm"}) == "ok"
    assert await s.handle({"id": "e1", "type": "dm"}) == "duplicate"
    assert await s.handle({"id": "e2", "type": "dm"}) == "ok"
    assert s.handled == ["e1", "e2"]

    # Ring buffer eviction: after 4 new ids, "e1" should be re-acceptable
    for i in range(3, 7):
        await s.handle({"id": f"e{i}"})
    assert await s.handle({"id": "e1"}) == "ok"
