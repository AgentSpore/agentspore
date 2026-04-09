"""Integration tests for the realtime stack with testcontainers (PG + Redis).

Covers:
  - AgentWebhookService.deliver: success path resets failures, failure path
    increments counter and inserts a dead-letter row (real PG schema).
  - Webhook auto-disable after AUTO_DISABLE_AFTER consecutive failures.
  - ConnectionManager cross-worker user channels via real Redis pub/sub.

Marked as `integration` — skipped automatically if Docker isn't available.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

try:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


# ── Shared containers (one PG + one Redis per test module) ────────────────


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="module")
def pg_async_url(pg_container):
    # testcontainers returns sync URL; rewrite for asyncpg
    raw = pg_container.get_connection_url()
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="module")
def redis_url(redis_container):
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture
async def session_maker(pg_async_url):
    """Create the slice of schema we need + return an async_sessionmaker bound to the test PG."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_async_url, future=True)
    async with engine.begin() as conn:
        # Minimal slice: only the columns/tables the webhook service touches.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";"))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                webhook_url TEXT,
                webhook_secret TEXT,
                webhook_failures_count INT NOT NULL DEFAULT 0,
                webhook_last_failure_at TIMESTAMPTZ,
                webhook_disabled BOOLEAN NOT NULL DEFAULT FALSE
            );
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS webhook_dead_letter (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL,
                last_error TEXT,
                attempts INT NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_dead_letter_event_id
                ON webhook_dead_letter (agent_id, event_id);
        """))

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture
async def insert_agent(session_maker):
    """Factory: insert an agent row with given webhook config and return its id."""
    from sqlalchemy import text

    created: list[str] = []

    async def _make(url: str | None, secret: str | None = None) -> str:
        agent_id = str(uuid.uuid4())
        async with session_maker() as s:
            await s.execute(
                text(
                    "INSERT INTO agents (id, webhook_url, webhook_secret) "
                    "VALUES (CAST(:id AS UUID), :url, :secret)"
                ),
                {"id": agent_id, "url": url, "secret": secret},
            )
            await s.commit()
        created.append(agent_id)
        return agent_id

    yield _make

    # Cleanup
    if created:
        async with session_maker() as s:
            await s.execute(
                text("DELETE FROM agents WHERE id = ANY(CAST(:ids AS UUID[]))"),
                {"ids": created},
            )
            await s.commit()


# ── Tiny aiohttp-free webhook receiver ────────────────────────────────────


class WebhookReceiver:
    """Plain asyncio HTTP/1.1 server that records requests and replies with a fixed status."""

    def __init__(self, status: int = 200):
        self.status = status
        self.requests: list[dict] = []
        self._server: asyncio.base_events.Server | None = None
        self.port: int = 0

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await reader.readline()
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b""):
                    break
                k, _, v = line.decode().partition(":")
                headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(length) if length else b""
            self.requests.append({
                "request_line": request_line.decode().strip(),
                "headers": headers,
                "body": body.decode("utf-8", errors="replace"),
            })
            reason = {200: "OK", 204: "No Content", 500: "Server Error"}.get(self.status, "OK")
            payload = b"" if self.status == 204 else b"ok"
            resp = (
                f"HTTP/1.1 {self.status} {reason}\r\n"
                f"Content-Length: {len(payload)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode() + payload
            writer.write(resp)
            await writer.drain()
        finally:
            writer.close()


# ── Webhook delivery integration tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_delivery_success_clears_failures(session_maker, insert_agent, monkeypatch):
    from sqlalchemy import text

    from app.services import agent_webhook_service as awm

    monkeypatch.setattr(awm, "async_session_maker", session_maker)

    agent_id = await insert_agent("http://placeholder", secret="topsecret")
    # Pre-set failure counter to verify it gets reset on success.
    async with session_maker() as s:
        await s.execute(
            text("UPDATE agents SET webhook_failures_count = 4 WHERE id = CAST(:id AS UUID)"),
            {"id": agent_id},
        )
        await s.commit()

    async with WebhookReceiver(status=204) as srv:
        # Re-point the webhook URL to the live receiver.
        async with session_maker() as s:
            await s.execute(
                text("UPDATE agents SET webhook_url = :u WHERE id = CAST(:id AS UUID)"),
                {"u": srv.url, "id": agent_id},
            )
            await s.commit()

        ok = await awm.AgentWebhookService.deliver(
            agent_id, {"type": "dm", "id": "evt-1", "content": "hi"}
        )

    assert ok is True
    assert len(srv.requests) == 1
    req = srv.requests[0]
    assert req["headers"]["x-agentspore-event"] == "dm"
    assert req["headers"]["x-agentspore-event-id"] == "evt-1"
    assert req["headers"]["x-agentspore-signature"].startswith("sha256=")
    assert json.loads(req["body"]) == {"type": "dm", "id": "evt-1", "content": "hi"}

    async with session_maker() as s:
        row = (await s.execute(
            text("SELECT webhook_failures_count, webhook_disabled FROM agents WHERE id = CAST(:id AS UUID)"),
            {"id": agent_id},
        )).first()
    assert row.webhook_failures_count == 0
    assert row.webhook_disabled is False


@pytest.mark.asyncio
async def test_webhook_delivery_failures_create_dead_letter(session_maker, insert_agent, monkeypatch):
    from sqlalchemy import text

    from app.services import agent_webhook_service as awm

    monkeypatch.setattr(awm, "async_session_maker", session_maker)
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(awm.asyncio, "sleep", lambda *_: _real_sleep(0))  # collapse backoff

    async with WebhookReceiver(status=500) as srv:
        agent_id = await insert_agent(srv.url, secret="s")
        ok = await awm.AgentWebhookService.deliver(
            agent_id, {"type": "dm", "id": "evt-fail", "content": "x"}
        )

    assert ok is False
    # 3 retry attempts hit the receiver
    assert len(srv.requests) == awm.AgentWebhookService.MAX_ATTEMPTS == 3

    async with session_maker() as s:
        row = (await s.execute(
            text("SELECT webhook_failures_count FROM agents WHERE id = CAST(:id AS UUID)"),
            {"id": agent_id},
        )).first()
        assert row.webhook_failures_count == 1

        dlq = (await s.execute(
            text("SELECT event_id, event_type, attempts, last_error FROM webhook_dead_letter "
                 "WHERE agent_id = CAST(:id AS UUID)"),
            {"id": agent_id},
        )).first()
    assert dlq is not None
    assert dlq.event_id == "evt-fail"
    assert dlq.event_type == "dm"
    assert dlq.attempts == 3
    assert "HTTP 500" in (dlq.last_error or "")


@pytest.mark.asyncio
async def test_webhook_auto_disables_after_threshold(session_maker, insert_agent, monkeypatch):
    from sqlalchemy import text

    from app.services import agent_webhook_service as awm

    monkeypatch.setattr(awm, "async_session_maker", session_maker)
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(awm.asyncio, "sleep", lambda *_: _real_sleep(0))

    async with WebhookReceiver(status=500) as srv:
        agent_id = await insert_agent(srv.url, secret="s")
        # Simulate 9 prior failures so the next failure crosses the threshold.
        async with session_maker() as s:
            await s.execute(
                text("UPDATE agents SET webhook_failures_count = :n WHERE id = CAST(:id AS UUID)"),
                {"n": awm.AgentWebhookService.AUTO_DISABLE_AFTER - 1, "id": agent_id},
            )
            await s.commit()

        ok = await awm.AgentWebhookService.deliver(
            agent_id, {"type": "dm", "id": "evt-disable"}
        )

    assert ok is False
    async with session_maker() as s:
        row = (await s.execute(
            text("SELECT webhook_failures_count, webhook_disabled FROM agents WHERE id = CAST(:id AS UUID)"),
            {"id": agent_id},
        )).first()
    assert row.webhook_failures_count == awm.AgentWebhookService.AUTO_DISABLE_AFTER
    assert row.webhook_disabled is True


@pytest.mark.asyncio
async def test_webhook_skips_delivery_when_disabled(session_maker, insert_agent, monkeypatch):
    from sqlalchemy import text

    from app.services import agent_webhook_service as awm

    monkeypatch.setattr(awm, "async_session_maker", session_maker)

    async with WebhookReceiver(status=200) as srv:
        agent_id = await insert_agent(srv.url, secret="s")
        async with session_maker() as s:
            await s.execute(
                text("UPDATE agents SET webhook_disabled = TRUE WHERE id = CAST(:id AS UUID)"),
                {"id": agent_id},
            )
            await s.commit()

        ok = await awm.AgentWebhookService.deliver(
            agent_id, {"type": "dm", "id": "evt-skip"}
        )
    assert ok is False
    assert srv.requests == []  # never even attempted


# ── ConnectionManager cross-worker user channel via real Redis ────────────


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        pass


@pytest.mark.asyncio
async def test_user_channel_cross_worker_via_real_redis(redis_url, monkeypatch):
    """Two ConnectionManager instances simulate two backend workers; an event
    published from worker A must reach a tab connected to worker B."""
    from redis.asyncio import from_url

    from app.services import connection_manager as cm_mod

    redis = await from_url(redis_url, decode_responses=True)

    # Hand both managers the same Redis client.
    async def _ensure(self):  # type: ignore[override]
        return redis

    monkeypatch.setattr(cm_mod.ConnectionManager, "_ensure_redis", _ensure)

    worker_a = cm_mod.ConnectionManager()
    worker_b = cm_mod.ConnectionManager()

    ws_b = FakeWS()
    await worker_b.connect_user("user-X", ws_b)

    # Give the pub/sub subscriber on worker B a moment to subscribe.
    await asyncio.sleep(0.2)

    await worker_a.send_user("user-X", {"type": "hosted_agent_status", "status": "running"})

    # Wait briefly for the message to traverse Redis.
    for _ in range(20):
        if ws_b.sent:
            break
        await asyncio.sleep(0.05)

    assert ws_b.sent, "Worker B should have received the event via Redis pub/sub"
    assert ws_b.sent[0]["type"] == "hosted_agent_status"
    assert ws_b.sent[0]["status"] == "running"
    # Origin-worker key must be stripped before forwarding to the client.
    assert "_origin_worker" not in ws_b.sent[0]

    await worker_b.disconnect_user("user-X", ws_b)
    await redis.aclose()
