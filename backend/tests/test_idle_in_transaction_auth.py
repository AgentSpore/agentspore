"""WS/SSE auth must not leave its session idle in transaction.

The WebSocket and SSE endpoints hold one ``get_db`` session for the whole
connection. ``authenticate_ws`` runs ``SELECT * FROM agents WHERE api_key_hash``
on it; SQLAlchemy autobegins a transaction, so unless the auth step ends it the
Postgres backend stays ``idle in transaction`` for as long as the agent stays
connected, which blocks ``CREATE INDEX CONCURRENTLY``.

Run:
    DOCKER_HOST=unix:///Users/$USER/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
    uv run pytest tests/test_idle_in_transaction_auth.py -v
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.agents_ws import authenticate_ws
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

_IDLE_IN_TX = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
    "AND state = 'idle in transaction'"
)


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def session_factory(pg_dsn):
    engine = create_async_engine(pg_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS agents"))
        await conn.execute(
            text(
                "CREATE TABLE agents (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
                "name text NOT NULL, api_key_hash text, is_active boolean NOT NULL DEFAULT TRUE)"
            )
        )
        await conn.execute(
            text("INSERT INTO agents (name, api_key_hash) VALUES ('probe', :h)"),
            {"h": AgentService.hash_api_key(_API_KEY)},
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize("key", [_API_KEY, "af_wrong_key"], ids=["valid", "invalid"])
async def test_authenticate_ws_ends_its_transaction(session_factory, key):
    async with session_factory() as long_lived, session_factory() as observer:
        agent = await authenticate_ws(key, long_lived)

        assert (agent is not None) == (key == _API_KEY)
        idle = (await observer.execute(_IDLE_IN_TX)).scalar_one()
        assert idle == 0, "auth lookup left a connection idle in transaction"
