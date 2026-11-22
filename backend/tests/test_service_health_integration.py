"""Integration: migration V119, probe task writes, and the service_live boundary.

Real Postgres (the SQL is what is under test); only outbound HTTP is mocked.

Requirements:
    DOCKER_HOST=unix:///Users/<user>/.colima/default/docker.sock
    TESTCONTAINERS_RYUK_DISABLED=true
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.background import ALL_TASKS, ServiceHealthTask
from app.repositories import project_repo
from app.services.service_health import run_health_checks

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TC = True
except ImportError:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers not installed")

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "V119__project_service_health.sql"
)

_SCHEMA_STMTS = [
    'CREATE EXTENSION IF NOT EXISTS "pgcrypto"',
    "CREATE TABLE agents (id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
    " name TEXT NOT NULL, handle TEXT)",
    """
    CREATE TABLE projects (
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
        recovery_provenance TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
]


async def _apply_migration(conn) -> None:
    # V119 has no dollar-quoting, so a plain split on ';' is exact.
    for stmt in _MIGRATION.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            await conn.execute(text(stmt))


@pytest.fixture(scope="module")
def pg_url():
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    with PostgresContainer("postgres:15") as pg:
        raw = pg.get_connection_url()
        yield raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
            "postgresql://", "postgresql+asyncpg://"
        )


@pytest_asyncio.fixture
async def engine(pg_url):
    eng = create_async_engine(pg_url)
    async with eng.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS projects, agents CASCADE"))
        for stmt in _SCHEMA_STMTS:
            await conn.execute(text(stmt))
        await conn.execute(text("INSERT INTO agents (name, handle) VALUES ('a', 'a')"))
    yield eng
    await eng.dispose()


@pytest.fixture
def maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(maker) -> AsyncSession:
    async with maker() as s:
        yield s


async def _add(session, *, deploy_url=None, health_url=None, repo_url=None) -> str:
    row = await session.execute(
        text("""
            INSERT INTO projects (title, creator_agent_id, deploy_url, repo_url, status)
            VALUES ('p-' || :n, (SELECT id FROM agents LIMIT 1), :d, :r, 'deployed')
            RETURNING id
        """),
        {"n": uuid.uuid4().hex[:6], "d": deploy_url, "r": repo_url},
    )
    pid = str(row.scalar_one())
    if health_url:
        await session.execute(
            text("UPDATE projects SET health_url = :h WHERE id = CAST(:i AS uuid)"),
            {"h": health_url, "i": pid},
        )
    await session.commit()
    return pid


async def _state(session, pid: str) -> dict:
    row = await session.execute(
        text("""
            SELECT service_checked_at, service_ok_at, service_http_status
            FROM projects WHERE id = CAST(:i AS uuid)
        """),
        {"i": pid},
    )
    return dict(row.mappings().one())


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestMigration:
    async def test_adds_columns_and_sets_pereklichka_health_url_by_slug(
        self, engine, session
    ) -> None:
        pere = await _add(session, repo_url="https://github.com/agentspore/pereklichka")
        other = await _add(session, repo_url="https://github.com/agentspore/pereklichka-fork")
        async with engine.begin() as conn:
            await _apply_migration(conn)
        row = await session.execute(
            text("SELECT id::text AS id, health_url FROM projects ORDER BY created_at")
        )
        urls = {r["id"]: r["health_url"] for r in row.mappings()}
        assert urls[pere] == "https://pereklichka.agentspore.com/privacy"
        assert urls[other] is None

    async def test_is_idempotent(self, engine, session) -> None:
        await _add(session, repo_url="https://github.com/agentspore/pereklichka")
        async with engine.begin() as conn:
            await _apply_migration(conn)
        async with engine.begin() as conn:
            await _apply_migration(conn)


@pytest_asyncio.fixture
async def migrated(engine, session):
    async with engine.begin() as conn:
        await _apply_migration(conn)


@pytest.mark.usefixtures("migrated")
class TestProbeTask:
    async def test_2xx_sets_ok_5xx_keeps_it_and_error_nulls_status(self, session, maker) -> None:
        ok = await _add(session, deploy_url="https://ok.agentspore.com/")
        bad = await _add(session, deploy_url="https://bad.agentspore.com/")
        down = await _add(session, deploy_url="https://down.agentspore.com/")

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host == "ok.agentspore.com":
                return httpx.Response(200)
            if host == "bad.agentspore.com":
                return httpx.Response(502)
            raise httpx.ConnectTimeout("t", request=request)

        async with _client(handler) as client:
            assert await run_health_checks(maker, client) == 3

        s_ok = await _state(session, ok)
        s_bad = await _state(session, bad)
        s_down = await _state(session, down)
        assert s_ok["service_ok_at"] is not None and s_ok["service_http_status"] == 200
        assert s_bad["service_ok_at"] is None and s_bad["service_http_status"] == 502
        assert s_bad["service_checked_at"] is not None
        assert s_down["service_ok_at"] is None and s_down["service_http_status"] is None
        assert s_down["service_checked_at"] is not None

    async def test_failure_after_success_keeps_previous_ok_at(self, session, maker) -> None:
        pid = await _add(session, deploy_url="https://a.agentspore.com/")
        async with _client(lambda r: httpx.Response(200)) as client:
            await run_health_checks(maker, client)
        first = (await _state(session, pid))["service_ok_at"]
        async with _client(lambda r: httpx.Response(500)) as client:
            await run_health_checks(maker, client)
        after = await _state(session, pid)
        assert after["service_ok_at"] == first and after["service_http_status"] == 500

    async def test_health_url_wins_and_foreign_urls_get_no_request_no_write(
        self, session, maker
    ) -> None:
        bot = await _add(
            session,
            deploy_url="https://t.me/bot",
            health_url="https://pereklichka.agentspore.com/privacy",
        )
        foreign = await _add(session, deploy_url="https://evil.io/")
        no_url = await _add(session)
        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            return httpx.Response(200)

        async with _client(handler) as client:
            assert await run_health_checks(maker, client) == 1
        await session.commit()

        assert requested == ["https://pereklichka.agentspore.com/privacy"]
        assert (await _state(session, bot))["service_ok_at"] is not None
        for pid in (foreign, no_url):
            assert (await _state(session, pid))["service_checked_at"] is None

    async def test_unbuildable_url_does_not_abort_the_other_rows(self, session, maker) -> None:
        bad = await _add(session, deploy_url="https://evil.io\n.agentspore.com/")
        good = await _add(session, deploy_url="https://ok.agentspore.com/")
        async with _client(lambda r: httpx.Response(200)) as client:
            await run_health_checks(maker, client)
        assert (await _state(session, good))["service_ok_at"] is not None
        assert (await _state(session, bad))["service_checked_at"] is None

    async def test_task_is_registered_with_five_minute_interval(self) -> None:
        assert ServiceHealthTask in ALL_TASKS
        assert ServiceHealthTask.interval_s == 300


@pytest.mark.usefixtures("migrated")
class TestServiceLiveInApi:
    async def _live_by_age(self, session, minutes: float | None) -> tuple[dict, dict]:
        pid = await _add(session, deploy_url="https://a.agentspore.com/")
        if minutes is not None:
            await session.execute(
                text("""
                    UPDATE projects SET service_ok_at = now() - make_interval(secs => :s)
                    WHERE id = CAST(:i AS uuid)
                """),
                {"s": minutes * 60, "i": pid},
            )
            await session.commit()
        listed = next(p for p in await project_repo.list_projects(session) if p["id"] == pid)
        detail = await project_repo.get_project_by_id(session, uuid.UUID(pid))
        return listed, detail

    @pytest.mark.parametrize(
        ("age_min", "live"), [(0, True), (14.98, True), (15.02, False), (600, False), (None, False)]
    )
    async def test_fifteen_minute_boundary(self, session, age_min, live) -> None:
        listed, detail = await self._live_by_age(session, age_min)
        assert listed["service_live"] is live
        assert detail["service_live"] is live
        assert (listed["service_ok_at"] is not None) is (age_min is not None)

    async def test_health_url_is_never_exposed(self, session) -> None:
        pid = await _add(session, health_url="https://secret.agentspore.com/h")
        listed = next(p for p in await project_repo.list_projects(session) if p["id"] == pid)
        detail = await project_repo.get_project_by_id(session, uuid.UUID(pid))
        for item in (listed, detail):
            assert "health_url" not in item
            assert "secret.agentspore.com" not in str(item)
