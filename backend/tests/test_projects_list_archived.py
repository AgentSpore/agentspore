"""The projects LIST and the platform STATS must count the same projects.

Measured live 2026-08-17: /api/v1/projects returned 29 rows (9 of them archived)
while the homepage and dashboard showed 20. Both numbers were honest about the
query behind them; the queries disagreed. get_platform_stats has
``WHERE status <> 'archived'`` (agent_repo.py:657) and the list endpoint had no
such condition, so a visitor comparing two pages of the same site saw two
different project counts.

Integration by necessity: what is under test is the WHERE clause the repository
builds, so the query runs against real Postgres. Mocking the session would test
the mock's idea of SQL.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.colima/default/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.repositories import project_repo

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
        handle TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title TEXT NOT NULL,
        description TEXT,
        category TEXT,
        creator_agent_id UUID NOT NULL REFERENCES agents(id),
        status TEXT DEFAULT 'proposed',
        votes_up INT DEFAULT 0,
        votes_down INT DEFAULT 0,
        deploy_url TEXT,
        repo_url TEXT,
        tech_stack TEXT[],
        hackathon_id UUID,
        github_stars INT DEFAULT 0,
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


async def _seed(session: AsyncSession, statuses: list[str]) -> None:
    agent = await session.execute(
        text("INSERT INTO agents (name, handle) VALUES ('a', 'a') RETURNING id")
    )
    agent_id = str(agent.mappings().first()["id"])
    for status in statuses:
        await session.execute(
            text(
                "INSERT INTO projects (title, creator_agent_id, status) "
                "VALUES (:t, :c, :s)"
            ),
            {"t": f"p-{uuid.uuid4().hex[:8]}", "c": agent_id, "s": status},
        )
    await session.commit()


async def _stats_project_count(session: AsyncSession) -> int:
    """The exact predicate get_platform_stats counts by (agent_repo.py:657)."""
    row = await session.execute(
        text("SELECT COUNT(*) AS n FROM projects WHERE status <> 'archived'")
    )
    return int(row.mappings().first()["n"])


# Mirrors the live measurement: 20 countable + 9 archived = 29 rows in the table.
_LIVE_SHAPE = ["building"] * 3 + ["shipped"] * 2 + ["deployed"] * 15 + ["archived"] * 9


class TestArchivedProjectsAreNotCountedTwice:
    async def test_default_list_matches_the_platform_stats_count(self, session) -> None:
        """The number a visitor sees on /projects equals the homepage number.

        MUTATION: drop the archived exclusion from list_projects and this goes
        red — 29 rows against a stats count of 20.
        """
        await _seed(session, _LIVE_SHAPE)

        listed = await project_repo.list_projects(session, limit=200)
        assert len(listed) == await _stats_project_count(session) == 20
        assert not [p for p in listed if p["status"] == "archived"], (
            "an archived project reached the default list"
        )

    async def test_archived_are_still_reachable_by_explicit_filter(self, session) -> None:
        """The projects page offers an 'archived' filter chip
        (frontend/src/app/projects/page.tsx:232), so excluding archived by
        default must not make them unreachable — asking for them by name still
        returns them.

        MUTATION: exclude archived unconditionally (ignoring the status filter)
        and this goes red with an empty list.
        """
        await _seed(session, _LIVE_SHAPE)

        archived = await project_repo.list_projects(session, limit=200, status="archived")
        assert len(archived) == 9
        assert {p["status"] for p in archived} == {"archived"}

    async def test_a_non_archived_status_filter_is_unaffected(self, session) -> None:
        """The exclusion must not narrow a filter that already excludes archived
        by construction."""
        await _seed(session, _LIVE_SHAPE)

        deployed = await project_repo.list_projects(session, limit=200, status="deployed")
        assert len(deployed) == 15
