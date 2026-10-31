"""Long-lived agent connections must not hold a Postgres transaction open.

The WS and SSE endpoints used to keep one request-scoped ``get_db`` session for
the whole connection. The agent API-key lookup (``SELECT * FROM agents WHERE
api_key_hash``) autobegins a transaction on it, so the backend stayed
``idle in transaction`` for as long as the agent stayed connected, which blocks
``CREATE INDEX CONCURRENTLY``. Early-return branches of the WS message handler
and the other SSE routes (/events/stream, /teams/{id}/stream) leaked the same way.

Streams are served by a real uvicorn thread: Starlette's TestClient buffers the
whole body and cannot hold an endless SSE response open.

Run:
    DOCKER_HOST=unix:///Users/$USER/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
    uv run pytest tests/test_idle_in_transaction_auth.py -v
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

from app.api.v1 import agents_ws, events, teams
from app.core.redis_client import get_redis
from app.services.agent_service import AgentService

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TC = True
except ImportError:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(
    not _HAS_TC or not os.environ.get("DOCKER_HOST"),
    reason="testcontainers + DOCKER_HOST required",
)

_API_KEY = "af_idle_tx_probe_key"
_TEAM_ID = uuid.uuid4()
_UNKNOWN_UUID = "00000000-0000-0000-0000-000000000000"

_IDLE_IN_TX = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
    "AND state = 'idle in transaction'"
)


class _FakePubSub:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe(self, *_):
        return None

    psubscribe = unsubscribe = subscribe

    async def aclose(self):
        return None

    async def get_message(self, **_):
        await asyncio.sleep(0.05)


class _FakeRedis:
    def pubsub(self):
        return _FakePubSub()


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
def engine(pg_dsn):
    # NullPool: connections are opened on whichever loop uses them (uvicorn
    # thread, TestClient portal, test loop) and never shared across loops.
    return create_async_engine(pg_dsn, poolclass=NullPool)


@pytest.fixture
def session_factory(engine, monkeypatch):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    for module in ("app.core.database", "app.api.v1.agents_ws", "app.api.v1.teams"):
        monkeypatch.setattr(f"{module}.async_session_maker", factory)
    return factory


@pytest.fixture
def seeded(engine, session_factory):
    async def seed():
        async with engine.begin() as conn:
            # A leaked transaction from a previous (failed) test would hold a lock on DROP.
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
            await conn.execute(text("DROP TABLE IF EXISTS agents, agent_teams"))
            await conn.execute(
                text(
                    "CREATE TABLE agents (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
                    "name text NOT NULL, handle text, api_key_hash text, "
                    "is_active boolean NOT NULL DEFAULT TRUE)"
                )
            )
            await conn.execute(
                text("CREATE TABLE agent_teams (id uuid PRIMARY KEY, name text, is_active boolean)")
            )
            await conn.execute(
                text(
                    "INSERT INTO agents (name, handle, api_key_hash) VALUES ('probe', 'probe', :h)"
                ),
                {"h": AgentService.hash_api_key(_API_KEY)},
            )
            await conn.execute(
                text("INSERT INTO agent_teams VALUES (:id, 't', TRUE)"), {"id": _TEAM_ID}
            )

    asyncio.run(seed())


def _idle_in_tx(engine, settle: float = 0.0) -> int:
    """Count idle-in-transaction backends, polling up to ``settle`` seconds for 0."""

    async def count() -> int:
        deadline = time.monotonic() + settle
        while True:
            async with engine.connect() as conn:
                n = (await conn.execute(_IDLE_IN_TX)).scalar_one()
            if n == 0 or time.monotonic() >= deadline:
                return n
            await asyncio.sleep(0.05)

    return asyncio.run(count())


@pytest.fixture
def app(monkeypatch, seeded):
    monkeypatch.setattr(agents_ws, "get_redis", AsyncMock(return_value=_FakeRedis()))
    monkeypatch.setattr(events, "get_redis", AsyncMock(return_value=_FakeRedis()))
    manager = SimpleNamespace(
        connect=lambda _agent_id, ws: ws.accept(), disconnect=AsyncMock(return_value=None)
    )
    monkeypatch.setattr(agents_ws, "get_connection_manager", lambda: manager)
    app = FastAPI()
    app.include_router(agents_ws.router)
    app.include_router(events.router)
    app.include_router(teams.router)
    app.dependency_overrides[get_redis] = lambda: _FakeRedis()
    return app


@pytest.fixture
def live_url(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.parametrize("key", [_API_KEY, "af_wrong_key"], ids=["valid", "invalid"])
async def test_authenticate_ws_leaves_no_transaction(session_factory, seeded, engine, key):
    agent = await agents_ws.authenticate_ws(key)

    assert (agent is not None) == (key == _API_KEY)
    async with engine.connect() as conn:
        assert (await conn.execute(_IDLE_IN_TX)).scalar_one() == 0


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/agents/events", {"X-API-Key": _API_KEY}),
        ("/events/stream", {"X-API-Key": _API_KEY}),
        (f"/teams/{_TEAM_ID}/stream", {}),
    ],
    ids=["agents-sse", "events-stream", "team-stream"],
)
def test_open_stream_holds_no_transaction(live_url, engine, path, headers):
    with httpx.stream("GET", live_url + path, headers=headers, timeout=5) as resp:
        assert resp.status_code == 200
        assert _idle_in_tx(engine, settle=1.0) == 0


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "send_dm", "to": _UNKNOWN_UUID, "content": "x"},
        {"type": "send_dm", "to": "probe", "content": "x"},
        {"type": "send_dm", "to": "nobody", "content": "x"},
    ],
    ids=["unknown-uuid", "self-dm", "reply-dm-error"],
)
def test_ws_early_return_holds_no_transaction(app, engine, msg):
    with TestClient(app).websocket_connect("/agents/ws", headers={"X-API-Key": _API_KEY}) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json(msg)
        assert ws.receive_json()["type"] == "error"
        assert _idle_in_tx(engine, settle=2.0) == 0


@pytest.mark.parametrize(
    ("path", "headers", "status"),
    [
        ("/agents/events", {"X-API-Key": "af_wrong_key"}, 401),
        ("/events/stream", {"X-API-Key": "af_wrong_key"}, 401),
        ("/events/stream", {}, 422),
    ],
    ids=["agents-sse-bad-key", "events-stream-bad-key", "events-stream-no-key"],
)
def test_rejected_stream_holds_no_transaction(live_url, engine, path, headers, status):
    resp = httpx.get(live_url + path, headers=headers, timeout=5)

    assert resp.status_code == status
    assert _idle_in_tx(engine, settle=1.0) == 0


def test_ws_bad_key_closes_4401_without_transaction(app, engine):
    bad_key = {"X-API-Key": "af_wrong_key"}
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(app).websocket_connect("/agents/ws", headers=bad_key):
            pass

    assert closed.value.code == 4401
    assert _idle_in_tx(engine, settle=1.0) == 0
