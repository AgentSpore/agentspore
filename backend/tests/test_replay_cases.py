"""Integration tests for replay_cases endpoint — prod-trace sampling ingestion.

Uses testcontainers Postgres so the migration SQL is exercised against a real DB.
Tests cover: insert, list, filter by agent_handle, auth enforcement (X-Runner-Key).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


# Minimal schema: hosted_agents (FK target) + replay_cases
SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    owner_user_id UUID,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS agents_handle_uidx ON agents(handle);

CREATE TABLE IF NOT EXISTS hosted_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    owner_user_id UUID,
    system_prompt TEXT NOT NULL DEFAULT '',
    model VARCHAR(200) DEFAULT 'test/model:free',
    status VARCHAR(20) DEFAULT 'stopped',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS replay_cases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    agent_handle    TEXT NOT NULL,
    model           TEXT NOT NULL,
    trace_id        TEXT,
    input_messages  JSONB NOT NULL,
    output_text     TEXT,
    tool_calls      JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms     INTEGER,
    status          TEXT NOT NULL CHECK (status IN ('completed', 'failed', 'truncated')),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_replay_cases_agent_captured ON replay_cases(hosted_agent_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_replay_cases_status ON replay_cases(status);
"""


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
async def db_session(pg_container):
    """Function-scoped async session — engine + schema created per test (IF NOT EXISTS guards)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import text

    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(async_url, future=True)
    async with engine.begin() as conn:
        for stmt in SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _create_hosted(db_session) -> str:
    """Insert a minimal agent + hosted_agent row and return hosted_agent id."""
    from sqlalchemy import text

    agent_id = str(uuid.uuid4())
    handle = f"testbot-{agent_id[:8]}"
    await db_session.execute(
        text("INSERT INTO agents (id, handle, name) VALUES (:id, :handle, :name)"),
        {"id": agent_id, "handle": handle, "name": "Test Bot"},
    )
    result = await db_session.execute(
        text("""
            INSERT INTO hosted_agents (agent_id, system_prompt)
            VALUES (:agent_id, '')
            RETURNING id
        """),
        {"agent_id": agent_id},
    )
    await db_session.commit()
    return str(result.scalar_one())


class TestReplayCaseRepo:
    @pytest.mark.asyncio
    async def test_insert_and_retrieve(self, db_session):
        """Insert one replay case, retrieve it back."""
        from app.repositories.replay_case_repo import ReplayCaseRepository
        from app.schemas.replay_case import ReplayCaseCreate

        hosted_id = await _create_hosted(db_session)
        repo = ReplayCaseRepository(db_session)

        payload = ReplayCaseCreate(
            hosted_agent_id=uuid.UUID(hosted_id),
            agent_handle="redditscout",
            model="gpt-4o-mini",
            trace_id="t-001",
            input_messages=[{"role": "user", "content": "hello"}],
            output_text="done",
            tool_calls=[{"tool": "execute"}],
            duration_ms=1200,
            status="completed",
            metadata={"session_id": "s-abc"},
        )
        created = await repo.create(payload)

        assert created.id is not None
        assert created.agent_handle == "redditscout"
        assert created.status == "completed"
        assert created.duration_ms == 1200
        assert created.trace_id == "t-001"
        assert created.tool_calls == [{"tool": "execute"}]

    @pytest.mark.asyncio
    async def test_list_returns_all(self, db_session):
        """list_by_agent without filter returns all rows."""
        from app.repositories.replay_case_repo import ReplayCaseRepository
        from app.schemas.replay_case import ReplayCaseCreate

        hosted_id = await _create_hosted(db_session)
        repo = ReplayCaseRepository(db_session)

        for i in range(3):
            await repo.create(
                ReplayCaseCreate(
                    hosted_agent_id=uuid.UUID(hosted_id),
                    agent_handle=f"bot{i}",
                    model="m",
                    input_messages=[],
                    status="completed",
                )
            )

        cases = await repo.list_by_agent()
        assert len(cases) >= 3

    @pytest.mark.asyncio
    async def test_filter_by_agent_handle(self, db_session):
        """list_by_agent(agent_handle=...) returns only matching rows."""
        from app.repositories.replay_case_repo import ReplayCaseRepository
        from app.schemas.replay_case import ReplayCaseCreate

        hosted_id = await _create_hosted(db_session)
        repo = ReplayCaseRepository(db_session)
        handle = f"unique-bot-{uuid.uuid4().hex[:6]}"

        await repo.create(
            ReplayCaseCreate(
                hosted_agent_id=uuid.UUID(hosted_id),
                agent_handle=handle,
                model="m",
                input_messages=[],
                status="completed",
            )
        )
        await repo.create(
            ReplayCaseCreate(
                hosted_agent_id=uuid.UUID(hosted_id),
                agent_handle="other-bot",
                model="m",
                input_messages=[],
                status="completed",
            )
        )

        filtered = await repo.list_by_agent(agent_handle=handle)
        assert len(filtered) == 1
        assert filtered[0].agent_handle == handle


class TestReplayCaseEndpointAuth:
    """Auth enforcement tests — no real DB needed, mocked service."""

    def _make_app(self, runner_key: str):
        """Build a minimal FastAPI app wired with the replay router."""
        import app.api.v1.internal_replay as replay_module
        from fastapi import FastAPI
        from app.api.v1.internal_replay import router

        test_app = FastAPI()
        test_app.include_router(router, prefix="/api/v1")
        return test_app, runner_key

    def test_missing_runner_key_returns_403(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app.core.config import Settings

        mock_settings = MagicMock(spec=Settings)
        mock_settings.agent_runner_key = "correct-key"

        with patch("app.api.v1.internal_replay.get_settings", return_value=mock_settings):
            from fastapi import FastAPI
            from app.api.v1.internal_replay import router as replay_router

            test_app = FastAPI()
            test_app.include_router(replay_router, prefix="/api/v1")
            client = TestClient(test_app, raise_server_exceptions=False)

            resp = client.post("/api/v1/internal/replay-cases", json={})
            assert resp.status_code == 403

    def test_wrong_runner_key_returns_403(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app.core.config import Settings

        mock_settings = MagicMock(spec=Settings)
        mock_settings.agent_runner_key = "correct-key"

        with patch("app.api.v1.internal_replay.get_settings", return_value=mock_settings):
            from fastapi import FastAPI
            from app.api.v1.internal_replay import router as replay_router

            test_app = FastAPI()
            test_app.include_router(replay_router, prefix="/api/v1")
            client = TestClient(test_app, raise_server_exceptions=False)

            resp = client.post(
                "/api/v1/internal/replay-cases",
                json={},
                headers={"X-Runner-Key": "wrong-key"},
            )
            assert resp.status_code == 403
