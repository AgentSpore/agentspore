"""A4 P4c: V110 lifecycle columns and the retirement sweep query on real PostgreSQL."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres

from app.repositories.deployment_repo import DeploymentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
MIGRATIONS = Path(__file__).resolve().parents[2] / "db/migrations"
OLD_STATUSES = ("queued", "building", "probing", "promoting", "active", "failed", "recovering")
INSERT = (
    "INSERT INTO project_deployments (id, project_id, idempotency_key, commit_sha, "
    "repository_url, status, created_at{columns}) VALUES (:id, :project, :key, :sha, "
    "'https://example.test/r', :status, clock_timestamp() - make_interval(secs => :created_ago)"
    "{values})"
)
SECONDS_AGO = "clock_timestamp() - make_interval(secs => CAST(:{name} AS double precision))"


async def insert_legacy(db: AsyncSession, project: UUID, status: str, created_ago: float) -> UUID:
    """Insert as the pre-V110 schema allows."""
    row_id = uuid4()
    await db.execute(
        text(INSERT.format(columns="", values="")),
        {
            "id": row_id,
            "project": project,
            "key": uuid4(),
            "sha": "a" * 40,
            "status": status,
            "created_ago": created_ago,
        },
    )
    return row_id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def legacy_world(deployment_postgres):
    """A database holding V94 rows of every old status, migrated by V110 afterwards."""
    admin = create_async_engine(
        deployment_postgres.get_connection_url().replace("psycopg2", "asyncpg"),
        isolation_level="AUTOCOMMIT",
    )
    async with admin.connect() as connection:
        await connection.execute(text("CREATE DATABASE legacy"))
    await admin.dispose()
    url = deployment_postgres.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url.rsplit("/", 1)[0] + "/legacy")
    ids: dict[str, UUID] = {}
    try:
        async with engine.begin() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            await driver.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            await driver.execute((MIGRATIONS / "V94__project_deployments.sql").read_text())
            await driver.execute(
                "CREATE TABLE service_routes (project_id uuid PRIMARY KEY, handle text, "
                "active_revision uuid)"
            )
        async with AsyncSession(engine) as db:
            for status in OLD_STATUSES:
                ids[status] = await insert_legacy(db, uuid4(), status, 10)
            routed = uuid4()
            ids["routed_old"] = await insert_legacy(db, routed, "active", 300)
            ids["routed_new"] = await insert_legacy(db, routed, "active", 200)
            ids["routed_newest"] = await insert_legacy(db, routed, "active", 100)
            await db.execute(
                text(
                    "INSERT INTO service_routes (project_id, handle, active_revision) "
                    "VALUES (:p, 'h', :r)"
                ),
                {"p": routed, "r": ids["routed_old"]},
            )
            unrouted = uuid4()
            ids["plain_old"] = await insert_legacy(db, unrouted, "active", 300)
            ids["plain_new"] = await insert_legacy(db, unrouted, "active", 100)
            await db.commit()
        async with engine.begin() as connection:
            raw = await connection.get_raw_connection()
            await raw.driver_connection.execute(
                (MIGRATIONS / "V110__project_deployments_lifecycle.sql").read_text()
            )
        yield engine, ids
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(deployment_engine) -> AsyncSession:
    async with AsyncSession(deployment_engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def add(db):
    """Insert a V110 row; `activated_ago` is seconds before the database clock."""

    async def insert(
        project: UUID, status: str, activated_ago: float | None = None, **cols
    ) -> UUID:
        if activated_ago is None and status in ("active", "superseded"):
            activated_ago = 0
        row_id = uuid4()
        await db.execute(
            text(
                INSERT.format(
                    columns=", activated_at, sandbox_id",
                    values=", " + SECONDS_AGO.format(name="activated_ago") + ", :sandbox",
                )
            ),
            {
                "id": row_id,
                "project": project,
                "key": uuid4(),
                "sha": "a" * 40,
                "status": status,
                "created_ago": 1000,
                "activated_ago": activated_ago,
                "sandbox": cols.get("sandbox_id"),
            },
        )
        await db.commit()
        return row_id

    return insert


async def test_migration_keeps_every_old_status(legacy_world):
    engine, ids = legacy_world
    async with AsyncSession(engine) as db:
        rows = await db.execute(text("SELECT id, status, activated_at FROM project_deployments"))
        by_id = {row.id: row for row in rows}
    for status in OLD_STATUSES:
        assert by_id[ids[status]].status == status
    assert by_id[ids["queued"]].activated_at is None
    assert by_id[ids["active"]].activated_at is not None


async def test_backfill_keeps_routed_row_else_newest(legacy_world):
    engine, ids = legacy_world
    async with AsyncSession(engine) as db:
        rows = await db.execute(text("SELECT id, status, activated_at FROM project_deployments"))
        by_id = {row.id: row for row in rows}
    assert by_id[ids["routed_old"]].status == "active"
    assert by_id[ids["routed_new"]].status == "superseded"
    assert by_id[ids["routed_newest"]].status == "superseded"
    assert by_id[ids["plain_old"]].status == "superseded"
    assert by_id[ids["plain_new"]].status == "active"
    assert all(by_id[ids[k]].activated_at is not None for k in ("routed_new", "plain_old"))


async def test_status_check_accepts_superseded_and_rejects_garbage(db, add):
    project = uuid4()
    await add(project, "superseded")
    with pytest.raises(IntegrityError):
        await add(project, "retired")
    await db.rollback()


@pytest.mark.parametrize("status", ["active", "superseded"])
async def test_active_or_superseded_requires_activation_time(db, status):
    with pytest.raises(IntegrityError, match="deployment_lifecycle_activated"):
        await insert_legacy(db, uuid4(), status, 0)
    await db.rollback()


async def test_second_active_row_in_project_is_rejected(db, add):
    project = uuid4()
    await add(project, "active")
    with pytest.raises(IntegrityError, match="deployment_one_active_per_project"):
        await add(project, "active")
    await db.rollback()


@pytest.mark.parametrize(("seconds_ago", "expected"), [(29, False), (31, True)])
async def test_retire_after_grace_not_before(db, add, seconds_ago, expected):
    project = uuid4()
    old = await add(project, "superseded", activated_ago=3600)
    await add(project, "active", activated_ago=seconds_ago)
    candidates = await DeploymentRepository(db).retire_candidates()
    assert (old in {row["id"] for row in candidates}) is expected


async def test_grace_is_a_parameter(db, add):
    project = uuid4()
    old = await add(project, "superseded", activated_ago=3600)
    await add(project, "active", activated_ago=100)
    repo = DeploymentRepository(db)
    assert old in {row["id"] for row in await repo.retire_candidates()}
    assert old not in {row["id"] for row in await repo.retire_candidates(timedelta(minutes=5))}


async def test_retire_candidates_expose_sandbox_identity(db, add):
    project = uuid4()
    old = await add(project, "superseded", activated_ago=3600, sandbox_id="c" * 64)
    await add(project, "active", activated_ago=300)
    rows = await DeploymentRepository(db).retire_candidates()
    assert [(row["id"], row["sandbox_id"]) for row in rows if row["id"] == old] == [(old, "c" * 64)]


async def test_retired_and_active_rows_are_not_candidates(db, add):
    project = uuid4()
    old = await add(project, "superseded", activated_ago=3600)
    new = await add(project, "active", activated_ago=300)
    repo = DeploymentRepository(db)
    ids = {row["id"] for row in await repo.retire_candidates()}
    assert old in ids and new not in ids
    assert await repo.mark_retired(old)
    await db.commit()
    assert old not in {row["id"] for row in await repo.retire_candidates()}
    assert not await repo.mark_retired(old)
    assert not await repo.mark_retired(new)


async def test_superseded_without_successor_is_kept(db, add):
    lone = await add(uuid4(), "superseded", activated_ago=3600)
    rows = await DeploymentRepository(db).retire_candidates()
    assert lone not in {row["id"] for row in rows}
