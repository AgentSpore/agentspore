"""Integration tests for forking + cron — testcontainers PG."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

import pytest

try:
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


MINIMAL_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL DEFAULT 'test@test.com',
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL,
    handle VARCHAR(100) UNIQUE NOT NULL,
    agent_type VARCHAR(20) DEFAULT 'external',
    model_provider VARCHAR(100) DEFAULT '',
    model_name VARCHAR(200) DEFAULT '',
    specialization VARCHAR(50) DEFAULT 'programmer',
    skills TEXT[] DEFAULT '{}',
    description TEXT DEFAULT '',
    api_key_hash VARCHAR(64),
    karma INTEGER DEFAULT 0,
    projects_created INTEGER DEFAULT 0,
    code_commits INTEGER DEFAULT 0,
    reviews_done INTEGER DEFAULT 0,
    fork_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    is_hosted BOOLEAN DEFAULT FALSE,
    owner_email VARCHAR(255),
    owner_user_id UUID,
    last_heartbeat TIMESTAMPTZ,
    dna_risk INTEGER DEFAULT 5,
    dna_speed INTEGER DEFAULT 5,
    dna_verbosity INTEGER DEFAULT 5,
    dna_creativity INTEGER DEFAULT 5,
    bio TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hosted_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    owner_user_id UUID,
    system_prompt TEXT NOT NULL,
    model VARCHAR(200) DEFAULT 'test/model:free',
    runtime VARCHAR(50) DEFAULT 'python-minimal',
    agent_api_key TEXT,
    status VARCHAR(20) DEFAULT 'stopped',
    memory_limit_mb INTEGER DEFAULT 256,
    heartbeat_enabled BOOLEAN DEFAULT TRUE,
    heartbeat_seconds INTEGER DEFAULT 3600,
    total_cost_usd FLOAT DEFAULT 0.0,
    budget_usd FLOAT DEFAULT 1.0,
    container_id VARCHAR(100),
    infra_host VARCHAR(100),
    infra_port INTEGER,
    session_history JSONB,
    forked_from_hosted_id UUID,
    forked_from_agent_name VARCHAR(200),
    is_public BOOLEAN DEFAULT TRUE,
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    cpu_limit FLOAT DEFAULT 0.5,
    UNIQUE (agent_id)
);

CREATE TABLE IF NOT EXISTS agent_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    file_path VARCHAR(500) NOT NULL,
    file_type VARCHAR(20) DEFAULT 'text',
    content TEXT,
    size_bytes INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (hosted_agent_id, file_path)
);

CREATE TABLE IF NOT EXISTS agent_cron_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hosted_agent_id UUID NOT NULL REFERENCES hosted_agents(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    cron_expression VARCHAR(100) NOT NULL,
    task_prompt TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    auto_start BOOLEAN DEFAULT TRUE,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    run_count INTEGER DEFAULT 0,
    max_runs INTEGER,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
"""


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def pg_async_url(pg_container):
    raw = pg_container.get_connection_url()
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture
async def db_session(pg_async_url):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


@pytest.fixture
def repo(db_session):
    from app.repositories.hosted_agent_repo import HostedAgentRepository
    return HostedAgentRepository(db_session)


async def _create_agent(db_session, name="TestBot", handle=None, is_hosted=False) -> dict:
    """Helper: insert an agent row."""
    from sqlalchemy import text
    handle = handle or name.lower().replace(" ", "-")
    agent_id = str(uuid.uuid4())
    api_key = f"af_test_{uuid.uuid4().hex[:16]}"
    await db_session.execute(text("""
        INSERT INTO agents (id, name, handle, api_key_hash, is_hosted, owner_email)
        VALUES (:id, :name, :handle, :hash, :hosted, 'test@test.com')
    """), {"id": agent_id, "name": name, "handle": handle, "hash": api_key[:20], "hosted": is_hosted})
    await db_session.commit()
    return {"id": agent_id, "name": name, "handle": handle, "api_key": api_key}


async def _create_hosted(db_session, agent_id, owner_id, prompt="Test prompt", is_public=True) -> str:
    """Helper: insert hosted_agents + files."""
    from sqlalchemy import text
    hosted_id = str(uuid.uuid4())
    await db_session.execute(text("""
        INSERT INTO hosted_agents (id, agent_id, owner_user_id, system_prompt, agent_api_key, is_public)
        VALUES (:id, :aid, :uid, :prompt, 'af_test_key', :public)
    """), {"id": hosted_id, "aid": agent_id, "uid": owner_id, "prompt": prompt, "public": is_public})

    # Add files
    for path, content, ftype in [
        ("AGENT.md", prompt, "config"),
        (".deep/memory/main/MEMORY.md", "", "memory"),
        ("agent.yaml", "include_todo: true\n", "config"),
    ]:
        await db_session.execute(text("""
            INSERT INTO agent_files (hosted_agent_id, file_path, content, file_type, size_bytes)
            VALUES (:hid, :path, :content, :type, :size)
        """), {"hid": hosted_id, "path": path, "content": content, "type": ftype, "size": len(content)})

    await db_session.commit()
    return hosted_id


# ── Fork tests ──


@pytest.mark.asyncio
async def test_list_forkable(db_session, repo):
    """Public hosted agents appear in forkable list."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "ForkableBot", is_hosted=True)
    await _create_hosted(db_session, agent["id"], user_id, is_public=True)

    result = await repo.list_forkable()
    names = [r["agent_name"] for r in result]
    assert "ForkableBot" in names


@pytest.mark.asyncio
async def test_private_agent_not_forkable(db_session, repo):
    """Private hosted agents don't appear in forkable list."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "PrivateBot", is_hosted=True)
    await _create_hosted(db_session, agent["id"], user_id, is_public=False)

    result = await repo.list_forkable()
    names = [r["agent_name"] for r in result]
    assert "PrivateBot" not in names


@pytest.mark.asyncio
async def test_get_public_by_agent_id(db_session, repo):
    """Can look up public hosted agent by platform agent_id."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "LookupBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id, is_public=True)

    result = await repo.get_public_by_agent_id(agent["id"])
    assert result is not None
    assert str(result["id"]) == hosted_id
    assert result["agent_name"] == "LookupBot"


@pytest.mark.asyncio
async def test_get_public_by_agent_id_private_returns_none(db_session, repo):
    """Private agent returns None from public lookup."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "HiddenBot", is_hosted=True)
    await _create_hosted(db_session, agent["id"], user_id, is_public=False)

    result = await repo.get_public_by_agent_id(agent["id"])
    assert result is None


@pytest.mark.asyncio
async def test_increment_fork_count(db_session, repo):
    """Fork count increments correctly."""
    agent = await _create_agent(db_session, "CountBot")

    await repo.increment_fork_count(agent["id"])
    await repo.increment_fork_count(agent["id"])

    from sqlalchemy import text
    result = await db_session.execute(
        text("SELECT fork_count FROM agents WHERE id = :id"), {"id": agent["id"]},
    )
    assert result.scalar() == 2


@pytest.mark.asyncio
async def test_list_files_with_content(db_session, repo):
    """list_files_with_content returns file content for cloning."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "FileBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id, prompt="My prompt")

    files = await repo.list_files_with_content(hosted_id)
    assert len(files) == 3
    paths = {f["file_path"] for f in files}
    assert "AGENT.md" in paths
    assert "agent.yaml" in paths

    agent_md = next(f for f in files if f["file_path"] == "AGENT.md")
    assert "My prompt" in agent_md["content"]


# ── Cron tests ──


@pytest.mark.asyncio
async def test_create_cron_task(db_session, repo):
    """Create cron task with valid expression."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "CronBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    from croniter import croniter
    cron = croniter("0 9 * * *", datetime.now(timezone.utc))
    next_run = cron.get_next(datetime)

    task = await repo.create_cron_task({
        "hosted_agent_id": hosted_id,
        "name": "Daily task",
        "cron_expression": "0 9 * * *",
        "task_prompt": "Do daily work",
        "enabled": True,
        "auto_start": True,
        "max_runs": None,
        "next_run_at": next_run,
    })

    assert task["name"] == "Daily task"
    assert task["cron_expression"] == "0 9 * * *"
    assert task["run_count"] == 0
    assert task["enabled"] is True


@pytest.mark.asyncio
async def test_list_cron_tasks(db_session, repo):
    """List cron tasks for an agent."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "ListCronBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    from croniter import croniter
    for name in ["Task A", "Task B"]:
        cron = croniter("*/10 * * * *", datetime.now(timezone.utc))
        await repo.create_cron_task({
            "hosted_agent_id": hosted_id, "name": name,
            "cron_expression": "*/10 * * * *", "task_prompt": f"Do {name}",
            "enabled": True, "auto_start": True, "max_runs": None,
            "next_run_at": cron.get_next(datetime),
        })

    tasks = await repo.list_cron_tasks(hosted_id)
    assert len(tasks) >= 2
    names = {t["name"] for t in tasks}
    assert "Task A" in names
    assert "Task B" in names


@pytest.mark.asyncio
async def test_update_cron_task(db_session, repo):
    """Update cron task fields."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "UpdateCronBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    from croniter import croniter
    cron = croniter("0 9 * * *", datetime.now(timezone.utc))
    task = await repo.create_cron_task({
        "hosted_agent_id": hosted_id, "name": "Old name",
        "cron_expression": "0 9 * * *", "task_prompt": "Old prompt",
        "enabled": True, "auto_start": True, "max_runs": None,
        "next_run_at": cron.get_next(datetime),
    })

    updated = await repo.update_cron_task(str(task["id"]), {"name": "New name", "enabled": False})
    assert updated["name"] == "New name"
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_delete_cron_task(db_session, repo):
    """Delete cron task."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "DelCronBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    from croniter import croniter
    cron = croniter("0 9 * * *", datetime.now(timezone.utc))
    task = await repo.create_cron_task({
        "hosted_agent_id": hosted_id, "name": "Delete me",
        "cron_expression": "0 9 * * *", "task_prompt": "x",
        "enabled": True, "auto_start": True, "max_runs": None,
        "next_run_at": cron.get_next(datetime),
    })

    ok = await repo.delete_cron_task(str(task["id"]))
    assert ok is True

    remaining = await repo.list_cron_tasks(hosted_id)
    ids = {str(t["id"]) for t in remaining}
    assert str(task["id"]) not in ids


@pytest.mark.asyncio
async def test_get_due_cron_tasks(db_session, repo):
    """Due tasks (next_run_at in the past) are returned."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "DueBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await repo.create_cron_task({
        "hosted_agent_id": hosted_id, "name": "Overdue task",
        "cron_expression": "* * * * *", "task_prompt": "Run now",
        "enabled": True, "auto_start": True, "max_runs": None,
        "next_run_at": past,
    })

    due = await repo.get_due_cron_tasks()
    names = [t["name"] for t in due]
    assert "Overdue task" in names


@pytest.mark.asyncio
async def test_due_disabled_not_returned(db_session, repo):
    """Disabled tasks are not returned as due."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "DisabledBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await repo.create_cron_task({
        "hosted_agent_id": hosted_id, "name": "Disabled task",
        "cron_expression": "* * * * *", "task_prompt": "Skip",
        "enabled": False, "auto_start": True, "max_runs": None,
        "next_run_at": past,
    })

    due = await repo.get_due_cron_tasks()
    names = [t["name"] for t in due]
    assert "Disabled task" not in names


@pytest.mark.asyncio
async def test_get_due_cron_tasks_atomic_claim(pg_async_url, db_session):
    """Two concurrent repos must not both receive the same due task.

    Simulates 2 uvicorn workers hitting get_due_cron_tasks at the same time.
    With FOR UPDATE SKIP LOCKED + UPDATE ... RETURNING, only one worker
    should grab each row; the other gets an empty list.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.repositories.hosted_agent_repo import HostedAgentRepository

    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "ClaimBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    repo_seed = HostedAgentRepository(db_session)
    await repo_seed.create_cron_task({
        "hosted_agent_id": hosted_id, "name": "Contended",
        "cron_expression": "* * * * *", "task_prompt": "run",
        "enabled": True, "auto_start": True, "max_runs": None,
        "next_run_at": past,
    })
    await db_session.commit()

    engine_a = create_async_engine(pg_async_url, future=True)
    engine_b = create_async_engine(pg_async_url, future=True)
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    async def worker(maker):
        async with maker() as sess:
            r = HostedAgentRepository(sess)
            return await r.get_due_cron_tasks()

    try:
        results_a, results_b = await asyncio.gather(
            worker(maker_a), worker(maker_b)
        )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    names_a = [t["name"] for t in results_a]
    names_b = [t["name"] for t in results_b]
    total = names_a.count("Contended") + names_b.count("Contended")
    assert total == 1, f"Task claimed by {total} workers: A={names_a} B={names_b}"


@pytest.mark.asyncio
async def test_mark_cron_run(db_session, repo):
    """mark_cron_run increments count and updates next_run."""
    user_id = str(uuid.uuid4())
    agent = await _create_agent(db_session, "MarkBot", is_hosted=True)
    hosted_id = await _create_hosted(db_session, agent["id"], user_id)

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    task = await repo.create_cron_task({
        "hosted_agent_id": hosted_id, "name": "Mark task",
        "cron_expression": "0 * * * *", "task_prompt": "x",
        "enabled": True, "auto_start": True, "max_runs": None,
        "next_run_at": past,
    })

    await repo.mark_cron_run(str(task["id"]), future, error=None)

    updated = await repo.get_cron_task(str(task["id"]))
    assert updated["run_count"] == 1
    assert updated["last_run_at"] is not None
    assert updated["last_error"] is None
