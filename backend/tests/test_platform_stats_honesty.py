"""Integration test: platform stats count real activity, not stale flags.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.colima/default/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
    uv run pytest tests/test_platform_stats_honesty.py -x
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.repositories.agent_repo import AgentRepository
from app.repositories.analytics_repo import get_overview_stats

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
    """
    CREATE TABLE IF NOT EXISTS feature_requests (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bug_reports (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hackathons (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_teams (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        is_active BOOLEAN DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_messages (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid()
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
        await conn.execute(
            text(
                "TRUNCATE agents, projects, feature_requests, bug_reports, "
                "hackathons, agent_teams, agent_messages CASCADE"
            )
        )
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


async def _insert_agent(session: AsyncSession, *, heartbeat_sql: str | None) -> str:
    """Insert an agent; heartbeat_sql is a raw SQL expression or None for NULL."""
    expr = heartbeat_sql if heartbeat_sql is not None else "NULL"
    row = await session.execute(
        text(f"INSERT INTO agents (name, last_heartbeat) VALUES (:name, {expr}) RETURNING id"),
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
async def test_active_agents_counts_recent_heartbeat_only(session):
    recent_id = await _insert_agent(session, heartbeat_sql="NOW() - INTERVAL '1 hour'")
    stale_id = await _insert_agent(session, heartbeat_sql="NOW() - INTERVAL '48 hours'")
    never_id = await _insert_agent(session, heartbeat_sql=None)

    repo = AgentRepository(db=session)
    stats = await repo.get_platform_stats()

    assert stats["active_agents"] == 1
    assert stats["total_agents"] == 3
    assert recent_id and stale_id and never_id


@pytest.mark.asyncio
async def test_archived_project_excluded_from_total_projects(session):
    creator = await _insert_agent(session, heartbeat_sql="NOW()")
    # Counts must differ: with one row each, an inverted predicate also
    # returns 1 and the test passes while counting exactly the wrong rows.
    await _insert_project(session, creator, status="building")
    await _insert_project(session, creator, status="deployed")
    await _insert_project(session, creator, status="archived")

    repo = AgentRepository(db=session)
    stats = await repo.get_platform_stats()

    assert stats["total_projects"] == 2


@pytest.mark.asyncio
async def test_overview_stats_matches_platform_stats_semantics(session):
    recent = await _insert_agent(session, heartbeat_sql="NOW() - INTERVAL '10 minutes'")
    await _insert_agent(session, heartbeat_sql="NOW() - INTERVAL '30 hours'")
    await _insert_project(session, recent, status="archived")
    await _insert_project(session, recent, status="deployed")

    overview = await get_overview_stats(session)
    platform = await AgentRepository(db=session).get_platform_stats()

    assert overview["active_agents"] == 1
    assert overview["total_projects"] == 1
    # The point of this test is that the two repos agree. Asserting only the
    # literals would let one drift to a different window and stay green.
    assert overview["active_agents"] == platform["active_agents"]
    assert overview["total_projects"] == platform["total_projects"]
