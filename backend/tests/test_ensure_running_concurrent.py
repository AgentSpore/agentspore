"""Integration test: ensure_running concurrency under real PG advisory locks.

Simulates two uvicorn workers racing ensure_running for the same hosted_id.
Each worker has its own engine connection (separate SQLAlchemy sessions) so
pg_try_advisory_xact_lock contention is real — no mocks for the DB layer.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.docker/run/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
    uv run pytest tests/test_ensure_running_concurrent.py -x
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


# ── Schema DDL ────────────────────────────────────────────────────────────────
# asyncpg does not support multiple statements in a single prepared statement,
# so each DDL statement must be executed individually.

_SCHEMA_STMTS = [
    'CREATE EXTENSION IF NOT EXISTS "pgcrypto"',
    """
    CREATE TABLE IF NOT EXISTS agents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        handle TEXT NOT NULL UNIQUE,
        model_provider TEXT DEFAULT 'openrouter',
        model_name TEXT DEFAULT 'test/model:free',
        is_active BOOLEAN DEFAULT TRUE,
        is_hosted BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hosted_agents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_id UUID REFERENCES agents(id),
        owner_user_id UUID NOT NULL,
        system_prompt TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT 'test/model:free',
        runtime TEXT NOT NULL DEFAULT 'python-minimal',
        status TEXT NOT NULL DEFAULT 'stopped',
        memory_limit_mb INT DEFAULT 256,
        heartbeat_enabled BOOLEAN DEFAULT TRUE,
        heartbeat_seconds INT DEFAULT 3600,
        stuck_loop_detection BOOLEAN DEFAULT FALSE,
        total_cost_usd NUMERIC DEFAULT 0,
        budget_usd NUMERIC,
        container_id TEXT,
        infra_host TEXT,
        session_history JSONB,
        started_at TIMESTAMPTZ,
        stopped_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        agent_api_key TEXT,
        forked_from_hosted_id UUID,
        forked_from_agent_name TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hosted_agent_files (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        hosted_agent_id UUID REFERENCES hosted_agents(id) ON DELETE CASCADE,
        file_path TEXT NOT NULL,
        content TEXT,
        file_type TEXT DEFAULT 'text',
        size_bytes INT DEFAULT 0,
        version INT DEFAULT 1,
        truncated BOOLEAN DEFAULT FALSE,
        is_binary BOOLEAN DEFAULT FALSE,
        updated_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(hosted_agent_id, file_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS owner_messages (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        hosted_agent_id UUID REFERENCES hosted_agents(id) ON DELETE CASCADE,
        sender_type TEXT NOT NULL,
        content TEXT NOT NULL,
        tool_calls JSONB,
        thinking TEXT,
        is_deleted BOOLEAN DEFAULT FALSE,
        edited_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
]


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pg_container():
    """Real Postgres container, shared across the module."""
    with PostgresContainer("postgres:15") as pg:
        yield pg


@pytest.fixture(scope="module")
def pg_url(pg_container):
    """asyncpg-compatible URL from testcontainers."""
    raw = pg_container.get_connection_url()
    return (
        raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
           .replace("postgresql://", "postgresql+asyncpg://")
    )


@pytest_asyncio.fixture
async def engine(pg_url):
    """Function-scoped async engine — avoids cross-loop issues with asyncpg.

    Schema is created fresh each test with IF NOT EXISTS guards so it's
    idempotent. Using function scope is simpler than fighting module-scope
    event loop mismatches between pytest-asyncio and asyncpg.
    """
    eng = create_async_engine(pg_url, pool_size=10, max_overflow=5)
    async with eng.begin() as conn:
        for stmt in _SCHEMA_STMTS:
            await conn.execute(text(stmt))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def hosted_id(engine):
    """Insert a fresh stopped hosted_agent row; yield its UUID; delete after test."""
    handle = f"test-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        agent_row = await conn.execute(
            text("INSERT INTO agents (name, handle) VALUES ('test-agent', :handle) RETURNING id"),
            {"handle": handle},
        )
        agent_id = agent_row.mappings().first()["id"]

        hosted_row = await conn.execute(
            text("""
                INSERT INTO hosted_agents (agent_id, owner_user_id, system_prompt)
                VALUES (:agent_id, gen_random_uuid(), 'test prompt')
                RETURNING id
            """),
            {"agent_id": agent_id},
        )
        hid = str(hosted_row.mappings().first()["id"])

    yield hid

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM hosted_agents WHERE id = :id"), {"id": hid})
        await conn.execute(text("DELETE FROM agents WHERE id = :id"), {"id": agent_id})


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_service_with_real_session(session: AsyncSession) -> object:
    """Build a HostedAgentService backed by a real AsyncSession.

    Only _start_agent_internal, Redis, and deliver_user_event are mocked.
    The PG advisory lock executes for real.
    """
    from app.services.hosted_agent_service import HostedAgentService
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    repo = HostedAgentRepository(db=session)

    openrouter = AsyncMock()
    openrouter.resolve_model = AsyncMock(return_value="test/model:free")
    openrouter.get_context_length = AsyncMock(return_value=128_000)

    openviking = AsyncMock()
    openviking.enabled = False

    settings = MagicMock()
    settings.agent_runner_url = ""
    settings.agent_runner_key = ""
    settings.oauth_redirect_base_url = "https://agentspore.com"

    svc = HostedAgentService.__new__(HostedAgentService)
    svc.repo = repo
    svc.agent_svc = AsyncMock()
    svc.openrouter = openrouter
    svc.openviking = openviking
    svc.runner_url = ""
    svc.settings = settings
    svc._starting_locks = OrderedDict()
    return svc


def _mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock()
    r.incr = AsyncMock()
    r.expire = AsyncMock()
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_ensure_running_starts_once(engine, hosted_id):
    """Two separate DB sessions (simulating 2 workers) race ensure_running.

    The pg_try_advisory_xact_lock is real — one session wins the lock and
    calls _start_agent_internal; the other falls into the 1-second polling
    loop and exits once DB status flips to 'running'.

    Only one _start_agent_internal should fire regardless of which session
    wins the advisory lock.
    """
    session_a = AsyncSession(engine)
    session_b = AsyncSession(engine)
    svc_a = _make_service_with_real_session(session_a)
    svc_b = _make_service_with_real_session(session_b)

    start_count = 0

    async def slow_start(hosted, skip_bootstrap=False):
        """300ms simulated runner call; marks DB running so poller exits."""
        nonlocal start_count
        start_count += 1
        await asyncio.sleep(0.3)
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE hosted_agents SET status = 'running' WHERE id = :id"),
                {"id": hosted_id},
            )

    with (
        patch.object(svc_a, "_start_agent_internal", side_effect=slow_start),
        patch.object(svc_b, "_start_agent_internal", side_effect=slow_start),
        patch("app.core.redis_client.get_redis", return_value=_mock_redis()),
        patch("app.services.hosted_agent_service.deliver_user_event", new_callable=AsyncMock),
    ):
        results = await asyncio.gather(
            svc_a.ensure_running(hosted_id, source="chat"),
            svc_b.ensure_running(hosted_id, source="chat"),
            return_exceptions=True,
        )

    await session_a.close()
    await session_b.close()

    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"Unexpected exceptions: {errors}"

    assert start_count == 1, (
        f"Expected exactly 1 _start_agent_internal call, got {start_count}. "
        "The advisory-lock loser should have polled DB instead of starting."
    )
    assert False in results, "Cold-start winner should return False"


@pytest.mark.asyncio
async def test_concurrent_ensure_running_five_workers(engine, hosted_id):
    """5 concurrent callers in the same process — asyncio.Event dedup fires.

    All 5 share the same _starting_locks dict (same process), so only the
    first coroutine acquires the lock; the other 4 wait on the asyncio.Event
    and check DB status when it fires.

    After the winner marks DB running the 4 waiters each re-check and
    return True (already running).
    """
    # Reset to stopped
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE hosted_agents SET status = 'stopped' WHERE id = :id"),
            {"id": hosted_id},
        )

    sessions = [AsyncSession(engine) for _ in range(5)]
    services = [_make_service_with_real_session(s) for s in sessions]

    # Share the same lock dict to simulate one uvicorn worker's event loop
    shared_locks: OrderedDict = OrderedDict()
    for svc in services:
        svc._starting_locks = shared_locks

    start_count = 0
    mock_redis = _mock_redis()

    async def slow_start(hosted, skip_bootstrap=False):
        nonlocal start_count
        start_count += 1
        await asyncio.sleep(0.1)
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE hosted_agents SET status = 'running' WHERE id = :id"),
                {"id": hosted_id},
            )

    patches = [
        patch.object(svc, "_start_agent_internal", side_effect=slow_start)
        for svc in services
    ]
    for p in patches:
        p.start()

    try:
        with (
            patch("app.core.redis_client.get_redis", return_value=mock_redis),
            patch("app.services.hosted_agent_service.deliver_user_event", new_callable=AsyncMock),
        ):
            results = await asyncio.gather(
                *[svc.ensure_running(hosted_id, source="chat") for svc in services],
                return_exceptions=True,
            )
    finally:
        for p in patches:
            p.stop()

    for s in sessions:
        await s.close()

    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"Unexpected exceptions: {errors}"

    assert start_count == 1, (
        f"Expected exactly 1 start across 5 workers (same loop), got {start_count}. "
        "asyncio.Event dedup should collapse workers 2-5 into waiters."
    )

    bools = [r for r in results if isinstance(r, bool)]
    assert len(bools) == 5
    assert results.count(False) == 1, f"Exactly 1 cold-start winner expected; got {results}"
    assert results.count(True) == 4, f"4 waiters should see running; got {results}"


@pytest.mark.asyncio
async def test_ensure_running_idempotent_when_already_running(engine, hosted_id):
    """Fast path: DB already says 'running' → returns True without acquiring any lock."""
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE hosted_agents SET status = 'running' WHERE id = :id"),
            {"id": hosted_id},
        )

    async with AsyncSession(engine) as session:
        svc = _make_service_with_real_session(session)

        with (
            patch.object(svc, "_start_agent_internal", new_callable=AsyncMock) as mock_start,
            patch("app.core.redis_client.get_redis", return_value=_mock_redis()),
        ):
            result = await svc.ensure_running(hosted_id, source="ws_event")

    assert result is True
    mock_start.assert_not_called()
