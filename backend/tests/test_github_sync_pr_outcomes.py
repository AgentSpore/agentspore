"""Integration test: GitHubSyncTask writes PR-outcome lessons into agent memory.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.colima/default/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
    uv run pytest tests/test_github_sync_pr_outcomes.py -x
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.background import GitHubSyncTask, _PRSyncCtx

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
            CREATE TABLE IF NOT EXISTS agent_activity (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                agent_id UUID NOT NULL,
                project_id UUID,
                action_type TEXT NOT NULL,
                description TEXT,
                metadata JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_activity_pr_outcome
                ON agent_activity (agent_id, (metadata->>'pr_key'))
                WHERE action_type = 'pr_outcome'
                  AND metadata->>'pr_key' IS NOT NULL
                  AND metadata->>'pr_key' <> ''
        """))
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("TRUNCATE agent_activity CASCADE"))
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


class _FakeGitHub:
    """Stubs list_commits_page (author lookup for PR head sha)."""

    def __init__(self, commits: list[dict]):
        self._commits = commits
        self.calls = 0

    async def list_commits_page(
        self, repo_name: str, branch: str = "main", page: int = 1, per_page: int = 100
    ):
        self.calls += 1
        return self._commits if page == 1 else []


class _FakeOpenViking:
    def __init__(self, ok: bool = True, fail_for: set[str] | None = None):
        self.enabled = True
        self.ok = ok
        self.fail_for = fail_for or set()
        self.written: list[tuple[str, str]] = []

    async def add_to_agent_session(self, agent_id: str, content: str) -> bool:
        if agent_id in self.fail_for:
            raise RuntimeError("openviking down")
        self.written.append((agent_id, content))
        return self.ok


AGENT_A = str(uuid.uuid4())
AGENT_B = str(uuid.uuid4())
PROJECT_1 = str(uuid.uuid4())
PROJECT_2 = str(uuid.uuid4())
PROJECT_3 = str(uuid.uuid4())


def _pr(number, merged_at=None, head_sha="abc1234"):
    return {
        "number": number,
        "title": f"PR {number}",
        "merged_at": merged_at,
        "head_sha": head_sha,
        "created_at": "2026-08-01T00:00:00Z",
        "url": f"https://github.com/org/repo/pull/{number}",
    }


@pytest.mark.asyncio
async def test_merged_pr_writes_lesson_once(session):
    github = _FakeGitHub([{"sha": "abc1234", "author": "agent-a"}])
    ov = _FakeOpenViking()
    agent_map = {"agent-a": AGENT_A}

    ctx = _PRSyncCtx(session, github, ov, PROJECT_1, "repo")
    closed = [_pr(1, merged_at="2026-08-02T00:00:00Z")]
    await GitHubSyncTask._sync_pr_outcomes(ctx, agent_map, closed, [])
    await session.commit()

    assert ov.written == [(AGENT_A, ov.written[0][1])]
    assert "won" not in ov.written[0][1]  # sanity: this is PR language, not battle language
    assert "PR 1" in ov.written[0][1]

    # Re-run the same cycle: dedup index must block a second write.
    ov.written.clear()
    await GitHubSyncTask._sync_pr_outcomes(ctx, agent_map, closed, [])
    await session.commit()
    assert ov.written == []


@pytest.mark.asyncio
async def test_other_agents_pr_not_recorded_for_wrong_agent(session):
    """A PR whose head commit has no matching author in agent_map (a PR the
    platform did not attribute to any agent) must record for NOBODY — not
    fall back to some other agent present in the map."""
    github = _FakeGitHub([{"sha": "zzz9999", "author": "someone-else"}])
    ov = _FakeOpenViking()
    agent_map = {"agent-a": AGENT_A, "agent-b": AGENT_B}

    ctx = _PRSyncCtx(session, github, ov, PROJECT_2, "repo")
    closed = [_pr(2, merged_at="2026-08-02T00:00:00Z", head_sha="zzz9999")]
    await GitHubSyncTask._sync_pr_outcomes(ctx, agent_map, closed, [])
    await session.commit()

    assert ov.written == []


@pytest.mark.asyncio
async def test_openviking_failure_does_not_stop_other_agents(session):
    """One agent's memory write raising must not prevent the next agent's write."""
    github = _FakeGitHub([
        {"sha": "aaa1111", "author": "agent-a"},
        {"sha": "bbb2222", "author": "agent-b"},
    ])
    ov = _FakeOpenViking(fail_for={AGENT_A})
    agent_map = {"agent-a": AGENT_A, "agent-b": AGENT_B}

    ctx = _PRSyncCtx(session, github, ov, PROJECT_3, "repo")
    closed = [
        _pr(3, merged_at="2026-08-02T00:00:00Z", head_sha="aaa1111"),
        _pr(4, merged_at="2026-08-02T00:00:00Z", head_sha="bbb2222"),
    ]
    await GitHubSyncTask._sync_pr_outcomes(ctx, agent_map, closed, [])
    await session.commit()

    assert ov.written == [(AGENT_B, ov.written[0][1])]
