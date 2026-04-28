"""Tests for chat session controls: rewind to checkpoint + clear/new session.

Covers the soft-delete repo helpers introduced for hosted-agent chat
session management. The runner-coupled service flow (start/stop/rewind
HTTP calls) is exercised via a mocked ``_call_runner`` so the test
suite does not need the agent-runner service running.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


MINIMAL_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS hosted_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL,
    owner_user_id UUID,
    system_prompt TEXT NOT NULL,
    model VARCHAR(200) DEFAULT 'test/model:free',
    status VARCHAR(20) DEFAULT 'stopped',
    session_history JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS owner_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    sender_type VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    thinking TEXT,
    edited_at TIMESTAMPTZ,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
async def db_session(pg_container):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import text

    pg_async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(pg_async_url, future=True)
    async with engine.begin() as conn:
        for stmt in MINIMAL_SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _create_hosted(db_session, status="running") -> str:
    from sqlalchemy import text

    hosted_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            """
            INSERT INTO hosted_agents (id, agent_id, system_prompt, status)
            VALUES (:id, :aid, 'You are a test agent', :st)
            """
        ),
        {"id": hosted_id, "aid": agent_id, "st": status},
    )
    await db_session.commit()
    return hosted_id


async def _add_message(db_session, hosted_id: str, sender: str, content: str, created_at: datetime | None = None):
    from sqlalchemy import text

    if created_at is None:
        await db_session.execute(
            text(
                """
                INSERT INTO owner_messages (hosted_agent_id, sender_type, content)
                VALUES (:hid, :s, :c)
                """
            ),
            {"hid": hosted_id, "s": sender, "c": content},
        )
    else:
        await db_session.execute(
            text(
                """
                INSERT INTO owner_messages (hosted_agent_id, sender_type, content, created_at)
                VALUES (:hid, :s, :c, :ts)
                """
            ),
            {"hid": hosted_id, "s": sender, "c": content, "ts": created_at},
        )
    await db_session.commit()


@pytest.fixture
def repo(db_session):
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    return HostedAgentRepository(db_session)


@pytest.mark.asyncio
async def test_soft_delete_owner_messages_after_hides_only_newer(db_session, repo):
    hosted_id = await _create_hosted(db_session)
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    await _add_message(db_session, hosted_id, "user", "old-1", base)
    await _add_message(db_session, hosted_id, "agent", "old-2", base + timedelta(minutes=1))
    cutoff = base + timedelta(minutes=2)
    await _add_message(db_session, hosted_id, "user", "new-1", base + timedelta(minutes=3))
    await _add_message(db_session, hosted_id, "agent", "new-2", base + timedelta(minutes=4))

    hidden = await repo.soft_delete_owner_messages_after(hosted_id, cutoff.isoformat())
    assert hidden == 2

    visible = await repo.get_owner_messages(hosted_id, limit=100)
    assert len(visible) == 2
    assert {m["content"] for m in visible} == {"old-1", "old-2"}


@pytest.mark.asyncio
async def test_soft_delete_owner_messages_after_idempotent(db_session, repo):
    hosted_id = await _create_hosted(db_session)
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    await _add_message(db_session, hosted_id, "user", "future", base + timedelta(minutes=5))

    cutoff = (base + timedelta(minutes=2)).isoformat()
    first = await repo.soft_delete_owner_messages_after(hosted_id, cutoff)
    second = await repo.soft_delete_owner_messages_after(hosted_id, cutoff)
    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_soft_delete_all_owner_messages_hides_everything(db_session, repo):
    hosted_id = await _create_hosted(db_session)
    for i in range(5):
        await _add_message(db_session, hosted_id, "user" if i % 2 == 0 else "agent", f"msg-{i}")

    hidden = await repo.soft_delete_all_owner_messages(hosted_id)
    assert hidden == 5

    visible = await repo.get_owner_messages(hosted_id, limit=100)
    assert visible == []


@pytest.mark.asyncio
async def test_soft_delete_all_does_not_touch_other_agents(db_session, repo):
    hosted_a = await _create_hosted(db_session)
    hosted_b = await _create_hosted(db_session)
    await _add_message(db_session, hosted_a, "user", "a-1")
    await _add_message(db_session, hosted_b, "user", "b-1")

    hidden = await repo.soft_delete_all_owner_messages(hosted_a)
    assert hidden == 1

    visible_a = await repo.get_owner_messages(hosted_a, limit=10)
    visible_b = await repo.get_owner_messages(hosted_b, limit=10)
    assert visible_a == []
    assert len(visible_b) == 1
    assert visible_b[0]["content"] == "b-1"


@pytest.mark.asyncio
async def test_clear_chat_service_clears_history_and_restarts_running_agent(db_session):
    """clear_chat() must soft-delete messages, reset session_history, and restart a running agent."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    from app.services.hosted_agent_service import HostedAgentService

    hosted_id = await _create_hosted(db_session, status="running")
    for i in range(3):
        await _add_message(db_session, hosted_id, "user", f"hi-{i}")

    repo = HostedAgentRepository(db_session)

    svc = HostedAgentService(
        repo=repo,
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )
    svc._call_runner = AsyncMock(return_value={"status": "ok"})
    svc.start_agent = AsyncMock(return_value={"status": "running"})
    svc.get_hosted_agent = AsyncMock(return_value={"id": hosted_id, "status": "running"})

    result = await svc.clear_chat(hosted_id, str(uuid.uuid4()))

    assert result["status"] == "ok"
    assert result["messages_hidden"] == 3
    assert result["agent_restarted"] is True
    svc._call_runner.assert_any_call("stop", hosted_id)
    svc.start_agent.assert_called_once()

    visible = await repo.get_owner_messages(hosted_id, limit=10)
    assert visible == []

    history = await repo.get_session_history(hosted_id)
    assert history == []


@pytest.mark.asyncio
async def test_rewind_service_hides_only_messages_after_checkpoint(db_session):
    """rewind_to_checkpoint() must call runner first, then soft-delete only newer messages."""
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    from app.services.hosted_agent_service import HostedAgentService

    hosted_id = await _create_hosted(db_session, status="running")
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    await _add_message(db_session, hosted_id, "user", "kept-1", base)
    await _add_message(db_session, hosted_id, "agent", "kept-2", base + timedelta(minutes=1))
    cutoff = base + timedelta(minutes=2)
    await _add_message(db_session, hosted_id, "user", "rolled-back-1", base + timedelta(minutes=3))
    await _add_message(db_session, hosted_id, "agent", "rolled-back-2", base + timedelta(minutes=4))

    repo = HostedAgentRepository(db_session)

    svc = HostedAgentService(
        repo=repo,
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )

    runner_responses = {
        "rewind": {"status": "ok", "checkpoint_id": "cp-1", "message_count": 4},
        "history": {"history": [{"kind": "request", "parts": []}]},
    }

    async def fake_call(action, hid, *args, **kwargs):
        return runner_responses[action]

    svc._call_runner = AsyncMock(side_effect=fake_call)
    svc.get_hosted_agent = AsyncMock(return_value={"id": hosted_id, "status": "running"})

    result = await svc.rewind_to_checkpoint(
        hosted_id, str(uuid.uuid4()), "cp-1", cutoff.isoformat()
    )

    assert result["status"] == "ok"
    assert result["messages_hidden"] == 2
    assert result["checkpoint_id"] == "cp-1"

    visible = await repo.get_owner_messages(hosted_id, limit=10)
    assert {m["content"] for m in visible} == {"kept-1", "kept-2"}
