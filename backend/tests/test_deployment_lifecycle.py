"""A4 P4c: V110 lifecycle columns, activation and the retirement sweep query on real PostgreSQL."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres

from app.repositories.deployment_repo import DeploymentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
V110 = Path(__file__).resolve().parents[2] / "db/migrations/V110__project_deployments_lifecycle.sql"
OLD_STATUSES = ("queued", "building", "probing", "promoting", "active", "failed", "recovering")
SHA = "a" * 40
T0 = datetime(2030, 1, 1, tzinfo=UTC)
Add = Callable[..., Awaitable[UUID]]


async def add_row(db: AsyncSession, project: UUID, status: str, **columns) -> UUID:
    row_id = uuid4()
    values = {
        "id": row_id,
        "project": project,
        "key": uuid4(),
        "sha": SHA,
        "status": status,
        "created": columns.pop("created_at", datetime.now(UTC)),
        "health": columns.pop("health_successes", 0),
    }
    await db.execute(
        text(
            "INSERT INTO project_deployments (id, project_id, idempotency_key, commit_sha, "
            "repository_url, status, health_successes, created_at) VALUES "
            "(:id, :project, :key, :sha, 'https://example.test/r', :status, :health, :created)"
        ),
        values,
    )
    if columns:
        await db.execute(
            text(
                "UPDATE project_deployments SET activated_at = :activated_at, "
                "sandbox_id = :sandbox_id WHERE id = :id"
            ),
            {
                "id": row_id,
                "activated_at": columns.get("activated_at"),
                "sandbox_id": columns.get("sandbox_id"),
            },
        )
    return row_id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def legacy_projects(deployment_engine) -> dict[str, UUID]:
    """Rows of every old status, plus two active rows of one project, written before V110."""
    ids: dict[str, UUID] = {}
    async with AsyncSession(deployment_engine) as db:
        for status in OLD_STATUSES:
            project = uuid4()
            ids[status] = await add_row(db, project, status)
        dup = uuid4()
        ids["dup_old"] = await add_row(db, dup, "active", created_at=T0 - timedelta(days=2))
        ids["dup_new"] = await add_row(db, dup, "active", created_at=T0 - timedelta(days=1))
        await db.commit()
    return ids


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated(deployment_engine, legacy_projects) -> dict[str, UUID]:
    async with deployment_engine.begin() as connection:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.execute(V110.read_text())
    return legacy_projects


@pytest_asyncio.fixture(loop_scope="module")
async def db(deployment_engine, migrated) -> AsyncSession:
    async with AsyncSession(deployment_engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def add(db) -> Add:
    async def insert(project: UUID, status: str, **columns) -> UUID:
        row_id = await add_row(db, project, status, **columns)
        await db.commit()
        return row_id

    return insert


async def statuses(db: AsyncSession, project: UUID) -> dict[UUID, str]:
    await db.rollback()
    rows = await db.execute(
        text("SELECT id, status FROM project_deployments WHERE project_id = :p"), {"p": project}
    )
    return {row.id: row.status for row in rows}


async def test_migration_keeps_old_rows_and_demotes_duplicate_active(db, migrated):
    rows = await db.execute(text("SELECT id, status, activated_at FROM project_deployments"))
    by_id = {row.id: row for row in rows}
    for status in OLD_STATUSES:
        assert by_id[migrated[status]].status == status
    assert by_id[migrated["dup_new"]].status == "active"
    assert by_id[migrated["dup_old"]].status == "superseded"
    assert by_id[migrated["dup_old"]].activated_at is not None
    assert by_id[migrated["queued"]].activated_at is None


async def test_status_check_accepts_superseded_and_rejects_garbage(db):
    project = uuid4()
    await add_row(db, project, "superseded")
    with pytest.raises(IntegrityError):
        await add_row(db, project, "retired")
    await db.rollback()


async def test_second_active_row_in_project_is_rejected(db, add):
    project = uuid4()
    await add(project, "active")
    with pytest.raises(IntegrityError):
        await add(project, "active")
    await db.rollback()


async def test_activate_supersedes_previous_active(db, add):
    project = uuid4()
    old = await add(project, "active")
    new = await add(project, "promoting", health_successes=3)
    assert await DeploymentRepository(db).activate(new)
    await db.commit()
    assert await statuses(db, project) == {old: "superseded", new: "active"}
    stamped = await db.scalar(
        text("SELECT activated_at IS NOT NULL FROM project_deployments WHERE id = :id"),
        {"id": new},
    )
    assert stamped is True


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(("probing", 0), id="not-promoting"),
        pytest.param(("promoting", 2), id="health-incomplete"),
        pytest.param(("active", 3), id="already-active"),
        pytest.param(("superseded", 3), id="superseded"),
    ],
)
async def test_activate_refuses_wrong_pre_state(db, add, state):
    project = uuid4()
    previous = await add(project, "failed")
    target = await add(project, state[0], health_successes=state[1])
    assert not await DeploymentRepository(db).activate(target)
    assert await statuses(db, project) == {previous: "failed", target: state[0]}


async def test_activate_unknown_deployment_is_refused(db):
    assert not await DeploymentRepository(db).activate(uuid4())


async def test_concurrent_activation_leaves_exactly_one_active(deployment_engine, db, add):
    project = uuid4()
    old = await add(project, "active")
    new = await add(project, "promoting", health_successes=3)

    async def attempt() -> bool:
        async with AsyncSession(deployment_engine) as session:
            won = await DeploymentRepository(session).activate(new)
            await session.commit()
            return won

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == [False, True]
    assert await statuses(db, project) == {old: "superseded", new: "active"}


async def seed_replacement(add: Add) -> tuple[UUID, UUID, UUID]:
    project = uuid4()
    old = await add(project, "superseded", activated_at=T0 - timedelta(hours=1))
    new = await add(project, "active", activated_at=T0)
    return project, old, new


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(29, False), (30, False), (31, True)],
)
async def test_retire_after_grace_not_before(db, add, seconds, expected):
    _, old, _ = await seed_replacement(add)
    candidates = await DeploymentRepository(db).retire_candidates(T0 + timedelta(seconds=seconds))
    assert (old in {row["id"] for row in candidates}) is expected


async def test_retire_candidates_expose_sandbox_identity(db, add):
    project = uuid4()
    old = await add(
        project, "superseded", activated_at=T0 - timedelta(hours=1), sandbox_id="c" * 64
    )
    await add(project, "active", activated_at=T0)
    rows = await DeploymentRepository(db).retire_candidates(T0 + timedelta(minutes=5))
    assert [(row["id"], row["sandbox_id"]) for row in rows if row["id"] == old] == [(old, "c" * 64)]


async def test_retired_rows_and_active_rows_are_not_candidates(db, add):
    _, old, new = await seed_replacement(add)
    repo = DeploymentRepository(db)
    later = T0 + timedelta(minutes=5)
    ids = {row["id"] for row in await repo.retire_candidates(later)}
    assert old in ids and new not in ids
    assert await repo.mark_retired(old)
    await db.commit()
    assert old not in {row["id"] for row in await repo.retire_candidates(later)}
    assert not await repo.mark_retired(old)
    assert not await repo.mark_retired(new)


async def test_superseded_without_successor_is_kept(db, add):
    project = uuid4()
    lone = await add(project, "superseded", activated_at=T0)
    rows = await DeploymentRepository(db).retire_candidates(T0 + timedelta(days=1))
    assert lone not in {row["id"] for row in rows}


async def test_activation_then_sweep_uses_real_clock(db, add):
    project = uuid4()
    old = await add(project, "active", activated_at=datetime.now(UTC) - timedelta(hours=1))
    new = await add(project, "promoting", health_successes=3)
    repo = DeploymentRepository(db)
    assert await repo.activate(new)
    await db.commit()
    now = datetime.now(UTC)
    assert old not in {row["id"] for row in await repo.retire_candidates(now)}
    soon = now + timedelta(seconds=31)
    assert old in {row["id"] for row in await repo.retire_candidates(soon)}


async def test_status_constraint_name_is_stable(db):
    name = await db.scalar(
        text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'project_deployments'::regclass "
            "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%superseded%'"
        )
    )
    assert name == "project_deployments_status_check"
