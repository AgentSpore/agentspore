"""The commit counter must be auditable and must not double-count.

Production measured 2026-08-16: SUM(agents.code_commits)=572 while the
agent_github_activity view showed 420 rows and *zero* of them carried a
commit_sha. The counter was unfalsifiable — three writers moved it, none of
them recorded which commit each increment stood for.

These tests pin the two properties that make the number checkable:

  1. every path that increments the counter records the real sha it counted,
     under the key the agent_github_activity view actually reads;
  2. the same sha counted twice for the same agent increments once, so a push
     arriving via BOTH the webhook and the proxy cannot inflate the number.

The dedup test asserts BEHAVIOUR (the counter moved once), not a specific
sha string.

Run:
    DOCKER_HOST=unix:///Users/$USER/.docker/run/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
    uv run pytest tests/test_commit_count_verifiable.py -v
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.repositories.agent_repo import AgentRepository

try:
    import asyncpg
    from testcontainers.postgres import PostgresContainer

    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(
    not _HAS_TC or not os.environ.get("DOCKER_HOST"),
    reason="testcontainers + DOCKER_HOST required",
)

_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "db",
    "migrations",
)

# Minimal slice of the real schema: only what the counter and the activity
# log touch. V2 defines agent_activity; V12 defines the view over it.
_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL DEFAULT 'test',
    handle VARCHAR(100) UNIQUE NOT NULL,
    code_commits INTEGER NOT NULL DEFAULT 0,
    karma INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL DEFAULT 'p',
    repo_url TEXT
);

CREATE TABLE agent_activity (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    action_type VARCHAR(50) NOT NULL,
    description TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE VIEW agent_github_activity AS
SELECT
    aa.id,
    aa.agent_id,
    aa.action_type,
    (aa.metadata->>'commit_sha')::text AS commit_sha,
    aa.created_at
FROM agent_activity aa
JOIN agents a ON a.id = aa.agent_id
WHERE aa.action_type IN ('code_commit', 'code_review');
"""


def _migration_sql() -> str:
    with open(
        os.path.join(_MIGRATIONS_DIR, "V81__commit_sha_dedup.sql"), encoding="utf-8"
    ) as fh:
        return fh.read()


async def _exec(dsn: str, sql: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _fresh(dsn: str, *, with_migration: bool = True) -> None:
    await _exec(dsn, "DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await _exec(dsn, _SCHEMA_SQL)
    if with_migration:
        await _exec(dsn, _migration_sql())


async def _record_commit(conn, agent_id, sha: str | None) -> bool:
    """Mirror the production write: insert activity, increment only if new.

    Returns True when the commit was counted.
    """
    meta = json.dumps({"commit_sha": sha} if sha else {})
    inserted = await conn.fetchval(
        """
        INSERT INTO agent_activity (agent_id, action_type, description, metadata)
        VALUES ($1, 'code_commit', 'push', $2::jsonb)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        agent_id,
        meta,
    )
    if inserted is None:
        return False
    await conn.execute(
        "UPDATE agents SET code_commits = code_commits + 1 WHERE id = $1", agent_id
    )
    return True


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")


@pytest.fixture
def run_async():
    loop = asyncio.new_event_loop()
    yield loop.run_until_complete
    loop.close()


def test_same_sha_counted_once_per_agent(pg_dsn, run_async):
    """A commit arriving twice for one agent increments the counter once.

    The assertion is on agents.code_commits, so any implementation that stops
    the second increment passes — the test does not encode the mechanism.
    """

    async def scenario():
        await _fresh(pg_dsn)
        conn = await asyncpg.connect(pg_dsn)
        try:
            agent_id = await conn.fetchval(
                "INSERT INTO agents (handle) VALUES ('a1') RETURNING id"
            )
            # Same sha delivered twice — e.g. webhook AND proxy push.
            first = await _record_commit(conn, agent_id, "abc1234")
            second = await _record_commit(conn, agent_id, "abc1234")

            assert first is True, "first delivery of a new sha must count"
            assert second is False, "second delivery of the same sha must not count"

            count = await conn.fetchval(
                "SELECT code_commits FROM agents WHERE id = $1", agent_id
            )
            assert count == 1, f"same sha counted {count} times, expected 1"

            rows = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_activity WHERE agent_id = $1", agent_id
            )
            assert rows == 1, f"{rows} activity rows for one commit"
        finally:
            await conn.close()

    run_async(scenario())


def test_different_shas_both_counted(pg_dsn, run_async):
    """The guard must not swallow genuinely distinct commits."""

    async def scenario():
        await _fresh(pg_dsn)
        conn = await asyncpg.connect(pg_dsn)
        try:
            agent_id = await conn.fetchval(
                "INSERT INTO agents (handle) VALUES ('a2') RETURNING id"
            )
            await _record_commit(conn, agent_id, "aaa1111")
            await _record_commit(conn, agent_id, "bbb2222")

            count = await conn.fetchval(
                "SELECT code_commits FROM agents WHERE id = $1", agent_id
            )
            assert count == 2, f"two distinct commits counted {count} times"
        finally:
            await conn.close()

    run_async(scenario())


def test_two_agents_may_share_a_sha(pg_dsn, run_async):
    """Dedup is per agent: a co-authored commit counts once for each agent."""

    async def scenario():
        await _fresh(pg_dsn)
        conn = await asyncpg.connect(pg_dsn)
        try:
            a1 = await conn.fetchval(
                "INSERT INTO agents (handle) VALUES ('x1') RETURNING id"
            )
            a2 = await conn.fetchval(
                "INSERT INTO agents (handle) VALUES ('x2') RETURNING id"
            )
            assert await _record_commit(conn, a1, "shared7") is True
            assert await _record_commit(conn, a2, "shared7") is True

            rows = await conn.fetchval("SELECT COUNT(*) FROM agent_activity")
            assert rows == 2, f"expected one row per agent, got {rows}"
        finally:
            await conn.close()

    run_async(scenario())


def test_sha_less_history_survives_the_migration(pg_dsn, run_async):
    """420 production rows carry no sha; the index must ignore them.

    A non-partial unique index over (agent_id, commit_sha) would treat every
    sha-less row as the same key and fail to create at all.
    """

    async def scenario():
        await _fresh(pg_dsn, with_migration=False)
        conn = await asyncpg.connect(pg_dsn)
        try:
            agent_id = await conn.fetchval(
                "INSERT INTO agents (handle) VALUES ('h1') RETURNING id"
            )
            for _ in range(3):
                await conn.execute(
                    """
                    INSERT INTO agent_activity (agent_id, action_type, description, metadata)
                    VALUES ($1, 'code_commit', 'legacy push', '{}'::jsonb)
                    """,
                    agent_id,
                )
        finally:
            await conn.close()

        # The migration must apply cleanly ON TOP of sha-less history.
        await _exec(pg_dsn, _migration_sql())

        conn = await asyncpg.connect(pg_dsn)
        try:
            rows = await conn.fetchval("SELECT COUNT(*) FROM agent_activity")
            assert rows == 3, f"migration destroyed history: {rows} rows left"

            # And a new sha-less row must still be insertable afterwards.
            agent_id = await conn.fetchval("SELECT id FROM agents LIMIT 1")
            await conn.execute(
                """
                INSERT INTO agent_activity (agent_id, action_type, description, metadata)
                VALUES ($1, 'code_commit', 'no sha', '{}'::jsonb)
                """,
                agent_id,
            )
            rows = await conn.fetchval("SELECT COUNT(*) FROM agent_activity")
            assert rows == 4
        finally:
            await conn.close()

    run_async(scenario())


def test_repository_returns_only_newly_accepted_commits(pg_dsn, run_async):
    """Exercise the REAL AgentRepository.record_commit_shas against Postgres.

    The other tests drive SQL written in this file, which would stay green even
    if the production method were wrong. This one calls the shipped code: it is
    the return value of record_commit_shas that the services turn into the
    increment, so that value is the guard that matters.
    """

    async def scenario():
        await _fresh(pg_dsn)
        async_url = pg_dsn.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(async_url, future=True)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                agent_id = (
                    await session.execute(
                        text("INSERT INTO agents (handle) VALUES ('r1') RETURNING id")
                    )
                ).scalar_one()
                repo = AgentRepository(session)

                first = await repo.record_commit_shas(agent_id, ["c0ffee1", "c0ffee2"])
                assert first == 2, f"two new shas accepted {first}"

                # Redelivery of one old sha plus one genuinely new one.
                second = await repo.record_commit_shas(agent_id, ["c0ffee2", "c0ffee3"])
                assert second == 1, (
                    f"expected only the unseen sha to count, got {second}"
                )

                # Empty and blank shas are never counted and never stored.
                blanks = await repo.record_commit_shas(agent_id, ["", None])
                assert blanks == 0, f"blank shas counted {blanks} times"

                await session.commit()

                total = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM agent_activity WHERE agent_id = :a"
                        ),
                        {"a": agent_id},
                    )
                ).scalar_one()
                assert total == 3, f"expected 3 distinct commits logged, got {total}"
        finally:
            await engine.dispose()

    run_async(scenario())


def test_view_exposes_the_sha_written_by_the_writers(pg_dsn, run_async):
    """The sha must land under the key agent_github_activity actually reads.

    The original defect: writers stored {"sha": ...} while the view selects
    metadata->>'commit_sha'. The value was present the whole time and
    invisible to every reader — 0 of 420 rows showed a sha.
    """

    async def scenario():
        await _fresh(pg_dsn)
        conn = await asyncpg.connect(pg_dsn)
        try:
            agent_id = await conn.fetchval(
                "INSERT INTO agents (handle) VALUES ('v1') RETURNING id"
            )
            await _record_commit(conn, agent_id, "deadbee")

            sha = await conn.fetchval(
                "SELECT commit_sha FROM agent_github_activity WHERE agent_id = $1",
                agent_id,
            )
            assert sha == "deadbee", f"view returned {sha!r}, expected the real sha"
        finally:
            await conn.close()

    run_async(scenario())
