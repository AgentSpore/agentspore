"""A2 acceptance harness: real PostgreSQL migrations, producer, and HTTP routes."""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.api.v1.agents import router as agents_router
from app.api.v1.projects import router as projects_router
from app.core.background import UsagePurgeTask
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.usage_repo import UsageRepository
from app.schemas.usage import UsageVisit
from app.services.agent_service import AgentService
from app.services.usage_service import UsageService

TODAY = date(2026, 10, 16)
BROWSER = "Mozilla/5.0 AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"
ZERO = {"uniques_1d": 0, "uniques_7d": 0, "retention_d7": 0.0}


@dataclass(frozen=True)
class UsageWorld:
    maker: async_sessionmaker
    projects: tuple[UUID, UUID, UUID]
    agents: tuple[UUID, UUID]
    owner: UUID
    keys: tuple[str, str]


@pytest.fixture(scope="session")
def usage_pg():
    with PostgresContainer(
        "postgres:16-alpine",
        mem_limit="384m",
        nano_cpus=500000000,
        labels={"a2.owner": "a2-usage-20261016"},
    ) as pg:
        yield pg


@pytest.fixture
async def usage_world(usage_pg):
    engine = create_async_engine(usage_pg.get_connection_url().replace("psycopg2", "asyncpg"))
    try:
        root = Path(__file__).resolve().parents[2] / "db/migrations"
        versioned = sorted(
            (p for p in root.glob("V*.sql") if re.match(r"V\d+__", p.name)),
            key=lambda p: int(p.name.split("__")[0][1:]),
        )
        migrations = versioned + sorted(root.glob("R__*.sql"))
        async with engine.begin() as conn:
            raw = await conn.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            await driver.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
            for migration in migrations:
                await driver.execute(migration.read_text())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        owner, agents, projects = (
            uuid4(),
            (uuid4(), uuid4()),
            (uuid4(), uuid4(), uuid4()),
        )
        keys = (str(uuid4()), str(uuid4()))
        async with maker.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, name) "
                    "VALUES (:id, 'a2@example.invalid', 'synthetic', 'A2')"
                ),
                {"id": owner},
            )
            for index, agent_id in enumerate(agents):
                await db.execute(
                    text(
                        "INSERT INTO agents "
                        "(id, name, handle, api_key_hash, owner_user_id) "
                        "VALUES (:id, :name, :handle, :hash, :owner)"
                    ),
                    {
                        "id": agent_id,
                        "name": f"A2-{index}",
                        "handle": f"a2-{index}",
                        "owner": owner if index == 0 else None,
                        "hash": AgentService.hash_api_key(keys[index]),
                    },
                )
            for index, project_id in enumerate(projects):
                await db.execute(
                    text(
                        "INSERT INTO projects (id, title, creator_agent_id) "
                        "VALUES (:id, 'A2 synthetic service', :agent)"
                    ),
                    {"id": project_id, "agent": agents[index // 2]},
                )
        yield UsageWorld(maker, projects, agents, owner, keys)
    finally:
        await engine.dispose()


@pytest.fixture
def usage_clock(monkeypatch):
    monkeypatch.setattr(UsageService, "today", staticmethod(lambda: TODAY))


@pytest.fixture
def visit():
    return UsageVisit(
        visitor_id=uuid4(),
        verified_user_id=None,
        user_agent=BROWSER,
        method="GET",
        accept="text/html",
        path="/",
    )


@pytest.fixture
async def usage_api(usage_world):
    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(agents_router, prefix="/api/v1")

    async def request_db():
        async with usage_world.maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_redis] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def cohort(usage_world):
    first, second, third, old = (uuid4() for _ in range(4))
    rows = [
        (first, 7),
        (second, 7),
        (first, 0),
        (third, 0),
        (third, 1),
        (second, 6),
        (old, 8),
        (uuid4(), -1),
    ]
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO service_visits (project_id, visitor_id, visit_day) "
                "VALUES (:project, :visitor, :day)"
            ),
            [
                {
                    "project": usage_world.projects[0],
                    "visitor": visitor,
                    "day": TODAY - timedelta(days=age),
                }
                for visitor, age in rows
            ],
        )
    return first


async def test_utc_windows_count_distinct_and_exact_d7_cohort(usage_world, cohort):
    async with usage_world.maker() as db:
        result = await UsageRepository(db).get_usage(usage_world.projects[0], TODAY)
        assert result is not None
        assert result.model_dump() == {
            "uniques_1d": 2,
            "uniques_7d": 3,
            "retention_d7": 0.5,
        }
        empty = await UsageRepository(db).get_usage(usage_world.projects[1], TODAY)
        assert empty is not None
        assert empty.model_dump() == ZERO


@pytest.mark.parametrize("offset", [timedelta(hours=-1), timedelta(hours=23)])
async def test_calendar_day_changes_only_at_utc_midnight(
    usage_world, visit, monkeypatch, offset, usage_api
):
    instant = datetime(2026, 10, 16, tzinfo=UTC) + offset
    monkeypatch.setattr(UsageService, "today", staticmethod(lambda: instant.date()))
    async with usage_world.maker.begin() as db:
        assert await UsageService(db).record_visit(usage_world.projects[0], visit) is True
        stored_day = await db.scalar(text("SELECT visit_day FROM service_visits"))
        assert stored_day == instant.date()
    response = await usage_api.get(f"/api/v1/projects/{usage_world.projects[0]}/usage")
    measurement = response.json()["measurement"]
    assert measurement["as_of_day"] == instant.date().isoformat()
    assert measurement["timezone"] == "UTC"
    assert measurement["d7"]["cohort_day"] == (instant.date() - timedelta(days=7)).isoformat()


@pytest.mark.parametrize(
    "patch",
    [
        {"user_agent": "Googlebot/2.1"},
        {"user_agent": "Mozilla/5.0 HeadlessChrome/129"},
        {"user_agent": "curl/8.1"},
        {"user_agent": "python-httpx/0.28"},
        {"user_agent": ""},
        {"accept": "application/json"},
        {"method": "HEAD"},
        {"path": "/health"},
        {"path": "/healthz"},
        {"path": "/metrics"},
    ],
)
async def test_bot_nonbrowser_and_health_traffic_writes_nothing(
    usage_world, usage_clock, visit, patch
):
    async with usage_world.maker.begin() as db:
        assert (
            await UsageService(db).record_visit(
                usage_world.projects[0],
                visit.model_copy(update=patch),
            )
            is False
        )
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0


@pytest.mark.parametrize("identity", ["owner", "stranger", "anonymous"])
async def test_only_verified_current_owner_is_excluded(usage_world, usage_clock, visit, identity):
    user_id = (
        usage_world.owner if identity == "owner" else uuid4() if identity == "stranger" else None
    )
    async with usage_world.maker.begin() as db:
        counted = await UsageService(db).record_visit(
            usage_world.projects[0],
            visit.model_copy(update={"verified_user_id": user_id}),
        )
        assert counted is (identity != "owner")
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == int(counted)


async def test_unknown_project_never_creates_orphan_visit(usage_world, usage_clock, visit):
    async with usage_world.maker.begin() as db:
        assert await UsageService(db).record_visit(uuid4(), visit) is False
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0


async def test_concurrent_same_visitor_is_counted_once(usage_world, usage_clock, visit):
    pids = []

    async def record():
        async with usage_world.maker.begin() as db:
            pids.append(await db.scalar(text("SELECT pg_backend_pid()")))
            return await UsageService(db).record_visit(usage_world.projects[0], visit)

    async with usage_world.maker() as blocker, usage_world.maker() as observer:
        assert await UsageService(blocker).record_visit(usage_world.projects[0], visit) is True
        async with asyncio.TaskGroup() as group:
            jobs = [group.create_task(record()) for _ in range(2)]
            for _ in range(200):
                await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                blocked = await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE "
                        "pid = ANY(CAST(:pids AS int[])) AND wait_event_type = 'Lock'"
                    ),
                    {"pids": pids},
                )
                if len(pids) == 2 and blocked == 2:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Both visit producers must reach the unique-row lock")
            await blocker.commit()
        assert [job.result() for job in jobs] == [False, False]
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 1


async def test_visitor_dedup_is_scoped_to_project_and_day(
    usage_world, usage_clock, visit, monkeypatch
):
    for project in usage_world.projects[:2]:
        async with usage_world.maker.begin() as db:
            assert await UsageService(db).record_visit(project, visit) is True
    monkeypatch.setattr(UsageService, "today", staticmethod(lambda: TODAY + timedelta(days=1)))
    async with usage_world.maker.begin() as db:
        assert await UsageService(db).record_visit(usage_world.projects[0], visit) is True
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 3


async def test_purge_removes_day_eight_and_preserves_d7_cohort(usage_world, cohort):
    async with usage_world.maker.begin() as db:
        assert await UsageRepository(db).purge_expired(TODAY) == 1
        assert await db.scalar(
            text("SELECT min(visit_day) FROM service_visits")
        ) == TODAY - timedelta(days=7)
        result = await UsageRepository(db).get_usage(usage_world.projects[0], TODAY)
        assert result is not None
        assert result.retention_d7 == 0.5


async def test_idle_scheduled_cleanup_purges_without_new_visits(
    usage_world,
    usage_clock,
    cohort,
    monkeypatch,
):
    monkeypatch.setattr("app.core.background.async_session_maker", usage_world.maker)
    await UsagePurgeTask().run_once()
    async with usage_world.maker() as db:
        assert await db.scalar(
            text("SELECT min(visit_day) FROM service_visits")
        ) == TODAY - timedelta(days=7)


async def test_visit_storage_has_no_ip_identity_or_request_columns(usage_world):
    async with usage_world.maker() as db:
        names = (
            await db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'service_visits'"
                )
            )
        ).scalars()
        assert set(names) == {"project_id", "visitor_id", "visit_day"}


async def test_public_usage_reads_aggregate_and_unknown_is_404(
    usage_api, usage_world, usage_clock, cohort
):
    response = await usage_api.get(f"/api/v1/projects/{usage_world.projects[0]}/usage")
    assert response.status_code == 200
    assert {key: response.json()[key] for key in ZERO} == {
        "uniques_1d": 2,
        "uniques_7d": 3,
        "retention_d7": 0.5,
    }
    assert response.json()["measurement"]["d7"]["observed_cohort_size"] == 2
    assert response.json()["measurement"]["d7"]["observed_returned_count"] == 1
    assert response.json()["measurement"]["d7"]["value"] == 0.5
    assert (await usage_api.get(f"/api/v1/projects/{uuid4()}/usage")).status_code == 404


async def test_heartbeat_returns_only_owned_projects_in_one_usage_query(
    usage_api,
    usage_world,
    usage_clock,
    cohort,
):
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO service_visits (project_id, visitor_id, visit_day) "
                "VALUES (:project, :visitor, :day)"
            ),
            {"project": usage_world.projects[2], "visitor": uuid4(), "day": TODAY},
        )
    statements = []
    engine = usage_world.maker.kw["bind"].sync_engine

    def capture(*args):
        statement, parameters = args[2:4]
        if "service_visits" in statement and statement.lstrip().upper().startswith(
            ("SELECT", "WITH")
        ):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await usage_api.post(
            "/api/v1/agents/heartbeat",
            json={"status": "idle"},
            headers={"X-API-Key": usage_world.keys[0]},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    payload = response.json()
    assert {row["project_id"] for row in payload["project_usage"]} == {
        str(project) for project in usage_world.projects[:2]
    }
    rows = {row["project_id"]: row for row in payload["project_usage"]}
    assert rows[str(usage_world.projects[0])] == {
        "project_id": str(usage_world.projects[0]),
        "uniques_1d": 2,
        "uniques_7d": 3,
        "retention_d7": 0.5,
    }
    assert rows[str(usage_world.projects[1])] == {
        "project_id": str(usage_world.projects[1]),
        **ZERO,
    }
    assert len(statements) == 1
    assert str(usage_world.agents[0]) in str(statements[0][1])
    assert "creator_agent_id" in statements[0][0]
    assert "tasks" in payload and "next_heartbeat_seconds" in payload


async def test_deleted_project_keeps_hostname_reserved_and_cannot_be_rebound(usage_world):
    project, other = usage_world.projects[:2]
    params = {"project": project, "other": other, "revision": uuid4()}
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("INSERT INTO service_hostnames VALUES ('a2-permanent', :project)"), params
        )
        await db.execute(
            text("INSERT INTO service_routes VALUES (:project, 'a2-permanent', :revision)"), params
        )
        await db.execute(text("DELETE FROM projects WHERE id = :project"), params)
        assert (
            await db.scalar(text("SELECT count(*) FROM service_routes WHERE handle LIKE 'a2-%'"))
            == 0
        )
        assert (
            await db.scalar(
                text("SELECT original_project_id FROM service_hostnames WHERE handle LIKE 'a2-%'")
            )
            == project
        )
        for statement, state in [
            ("INSERT INTO service_hostnames VALUES ('a2-permanent', :other)", "23505"),
            ("INSERT INTO service_routes VALUES (:other, 'a2-permanent', :revision)", "23503"),
        ]:
            with pytest.raises(IntegrityError) as error:
                async with db.begin_nested():
                    await db.execute(text(statement), params)
            assert getattr(error.value.orig, "sqlstate", None) == state
    async with usage_world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT original_project_id FROM service_hostnames WHERE handle LIKE 'a2-%'")
            )
            == project
        )
        assert (
            await db.scalar(text("SELECT count(*) FROM service_routes WHERE handle LIKE 'a2-%'"))
            == 0
        )


async def test_registry_identity_is_immutable_but_active_revision_can_change(usage_world):
    project, other = usage_world.projects[:2]
    params = {"project": project, "other": other, "revision": uuid4()}
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("INSERT INTO service_hostnames VALUES ('a2-identity', :project)"), params
        )
        await db.execute(
            text("INSERT INTO service_routes VALUES (:project, 'a2-identity', :revision)"), params
        )
        for statement in [
            "UPDATE service_hostnames SET handle = 'a2-replaced'",
            "UPDATE service_hostnames SET original_project_id = :other",
            "DELETE FROM service_hostnames",
            "UPDATE service_routes SET project_id = :other",
            "UPDATE service_routes SET handle = 'a2-replaced'",
        ]:
            with pytest.raises(IntegrityError) as error:
                async with db.begin_nested():
                    await db.execute(text(statement), params)
            assert getattr(error.value.orig, "sqlstate", None) == "23514"
        params["revision"] = uuid4()
        await db.execute(
            text("UPDATE service_routes SET active_revision = :revision WHERE handle LIKE 'a2-%'"),
            params,
        )
    async with usage_world.maker() as db:
        row = (
            (await db.execute(text("SELECT * FROM service_routes WHERE handle LIKE 'a2-%'")))
            .mappings()
            .one()
        )
        assert dict(row) == {
            "project_id": project,
            "handle": "a2-identity",
            "active_revision": params["revision"],
        }
        assert (
            await db.scalar(
                text("SELECT original_project_id FROM service_hostnames WHERE handle LIKE 'a2-%'")
            )
            == project
        )


async def test_repeatable_registry_sql_can_run_twice_and_preserves_identity_guards(usage_world):
    migration = (
        Path(__file__).resolve().parents[2] / "db/migrations/R__service_route_identity.sql"
    ).read_text()
    project = usage_world.projects[0]
    params = {"project": project, "revision": uuid4()}
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("INSERT INTO service_hostnames VALUES ('a2-repeatable', :project)"), params
        )
        await db.execute(
            text("INSERT INTO service_routes VALUES (:project, 'a2-repeatable', :revision)"), params
        )
        connection = await db.connection()
        raw = await connection.get_raw_connection()
        assert raw.driver_connection is not None
        for _ in range(2):
            await raw.driver_connection.execute(migration)
        for statement in [
            "DELETE FROM service_hostnames",
            "UPDATE service_routes SET handle = 'a2-replaced'",
        ]:
            with pytest.raises(IntegrityError) as error:
                async with db.begin_nested():
                    await db.execute(text(statement))
            assert getattr(error.value.orig, "sqlstate", None) == "23514"
        params["revision"] = uuid4()
        await db.execute(
            text("UPDATE service_routes SET active_revision = :revision WHERE handle LIKE 'a2-%'"),
            params,
        )
    async with usage_world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT original_project_id FROM service_hostnames WHERE handle LIKE 'a2-%'")
            )
            == project
        )
        assert (
            await db.scalar(
                text("SELECT active_revision FROM service_routes WHERE handle LIKE 'a2-%'")
            )
            == params["revision"]
        )


@pytest.mark.parametrize("cohort_case", [(0, 0, None), (2, 0, 0.0), (2, 1, 0.5)])
async def test_usage_report_distinguishes_empty_zero_and_returning_cohort(
    usage_api, usage_world, usage_clock, cohort_case
):
    size, returned, value = cohort_case
    project = usage_world.projects[0]
    visitors = [uuid4() for _ in range(size)]
    rows = [
        {"project": project, "visitor": visitor, "day": TODAY - timedelta(days=7)}
        for visitor in visitors
    ]
    rows.extend(
        {"project": project, "visitor": visitor, "day": TODAY} for visitor in visitors[:returned]
    )
    async with usage_world.maker.begin() as db:
        if rows:
            await db.execute(
                text(
                    "INSERT INTO service_visits (project_id,visitor_id,visit_day) "
                    "VALUES (:project,:visitor,:day)"
                ),
                rows,
            )
    statements = []
    engine = usage_world.maker.kw["bind"].sync_engine

    def capture(*args):
        if "service_visits" in args[2]:
            statements.append(args[2])

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await usage_api.get(f"/api/v1/projects/{project}/usage")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200 and len(statements) == 1
    payload = response.json()
    assert type(payload["uniques_1d"]) is int and type(payload["uniques_7d"]) is int
    assert type(payload["retention_d7"]) is float
    assert all(str(visitor) not in response.text for visitor in visitors)
    assert {key: payload[key] for key in ZERO} == {
        "uniques_1d": returned,
        "uniques_7d": returned,
        "retention_d7": value or 0.0,
    }
    assert payload["measurement"] == {
        "as_of_day": TODAY.isoformat(),
        "timezone": "UTC",
        "coverage": {"status": "unverified", "since": None},
        "d7": {
            "cohort_day": (TODAY - timedelta(days=7)).isoformat(),
            "observed_cohort_size": size,
            "observed_returned_count": returned,
            "value": value,
        },
    }
    assert not {"visitor_id", "verified_user_id", "ip"} & payload.keys()
