"""Integration test: platform stats drop dead metrics and name flags honestly.

feature_requests/bug_reports have no writer anywhere in the backend — the
counts are permanently zero and must not be reported. total_deploys counts
projects.status = 'deployed' (a state flag, not an event count) and must be
named to say so, not implied to be a running deploy counter.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.colima/default/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
    uv run pytest tests/test_platform_stats_no_dead_metrics.py -x
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.repositories.agent_repo import AgentRepository

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


_SCHEMA_STMTS = [
    'CREATE EXTENSION IF NOT EXISTS "pgcrypto"',
    """
    CREATE TABLE IF NOT EXISTS agents (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        code_commits INT DEFAULT 0,
        reviews_done INT DEFAULT 0,
        last_heartbeat TIMESTAMPTZ,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title TEXT NOT NULL,
        creator_agent_id UUID NOT NULL REFERENCES agents(id),
        status TEXT DEFAULT 'proposed',
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
]


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:15") as pg:
        yield pg


@pytest.fixture(scope="module")
def pg_url(pg_container):
    raw = pg_container.get_connection_url()
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest_asyncio.fixture
async def engine(pg_url):
    eng = create_async_engine(pg_url, pool_size=5, max_overflow=5)
    async with eng.begin() as conn:
        for stmt in _SCHEMA_STMTS:
            await conn.execute(text(stmt))
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("TRUNCATE agents, projects CASCADE"))
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


async def _insert_agent(session: AsyncSession) -> str:
    row = await session.execute(
        text("INSERT INTO agents (name) VALUES (:name) RETURNING id"),
        {"name": f"agent-{uuid.uuid4().hex[:8]}"},
    )
    await session.commit()
    return str(row.mappings().first()["id"])


async def _insert_project(session: AsyncSession, creator_agent_id: str, status: str) -> str:
    row = await session.execute(
        text(
            "INSERT INTO projects (title, creator_agent_id, status) "
            "VALUES (:title, :creator, :status) RETURNING id"
        ),
        {"title": f"project-{uuid.uuid4().hex[:8]}", "creator": creator_agent_id, "status": status},
    )
    await session.commit()
    return str(row.mappings().first()["id"])


@pytest.mark.asyncio
async def test_platform_stats_has_no_feature_request_or_bug_report_fields(session):
    """A dead metric that is never written must not be reported as a fact."""
    await _insert_agent(session)

    stats = await AgentRepository(db=session).get_platform_stats()

    assert "total_feature_requests" not in stats
    assert "total_bug_reports" not in stats


@pytest.mark.asyncio
async def test_deployed_projects_flag_is_named_for_what_it_counts(session):
    """The field counts a status flag, not a deploy event — the name must say so."""
    creator = await _insert_agent(session)
    await _insert_project(session, creator, status="deployed")
    await _insert_project(session, creator, status="deployed")
    await _insert_project(session, creator, status="building")

    stats = await AgentRepository(db=session).get_platform_stats()

    assert "total_deploys" not in stats
    assert stats["projects_deployed"] == 2
