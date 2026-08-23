"""Integration test: GitHubSyncTask writes a real merged-PR count, GREATEST-guarded.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.colima/default/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
    uv run pytest tests/test_github_sync_merged_prs.py -x
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.background import GitHubSyncTask

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")


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
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projects (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title TEXT NOT NULL,
                repo_url TEXT,
                merged_prs_count INT NOT NULL DEFAULT 0
            )
        """))
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("TRUNCATE projects CASCADE"))
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


class _FakeGitHub:
    """Stubs only the one method _sync_merged_prs calls."""

    def __init__(self, closed_prs: list[dict]):
        self._closed_prs = closed_prs

    async def list_pull_requests(self, repo_name: str, state: str = "open") -> list[dict]:
        assert state == "closed"
        return self._closed_prs


async def _insert_project(session: AsyncSession) -> str:
    row = await session.execute(
        text("INSERT INTO projects (title) VALUES (:title) RETURNING id"),
        {"title": f"project-{uuid.uuid4().hex[:8]}"},
    )
    await session.commit()
    return str(row.mappings().first()["id"])


@pytest.mark.asyncio
async def test_counts_only_prs_with_merged_at(session):
    project_id = await _insert_project(session)
    github = _FakeGitHub([
        {"number": 1, "merged_at": "2026-08-01T00:00:00Z"},
        {"number": 2, "merged_at": None},  # closed without merging
        {"number": 3, "merged_at": "2026-08-02T00:00:00Z"},
    ])

    await GitHubSyncTask._sync_merged_prs(session, github, project_id, "repo")
    await session.commit()

    row = await session.execute(
        text("SELECT merged_prs_count FROM projects WHERE id = :id"), {"id": project_id}
    )
    assert row.mappings().first()["merged_prs_count"] == 2


@pytest.mark.asyncio
async def test_greatest_guard_never_lowers_the_count(session):
    project_id = await _insert_project(session)
    await session.execute(
        text("UPDATE projects SET merged_prs_count = 5 WHERE id = :id"), {"id": project_id}
    )
    await session.commit()

    # A cycle that observes fewer merged PRs than before (e.g. repo
    # temporarily returned a partial page) must not erase the prior count.
    github = _FakeGitHub([{"number": 1, "merged_at": "2026-08-01T00:00:00Z"}])
    await GitHubSyncTask._sync_merged_prs(session, github, project_id, "repo")
    await session.commit()

    row = await session.execute(
        text("SELECT merged_prs_count FROM projects WHERE id = :id"), {"id": project_id}
    )
    assert row.mappings().first()["merged_prs_count"] == 5
