"""Tests for phase-0 durable delivery (V65 agent_events).

One test per build-step invariant:
  1 migration      — stable event_id; deliver/ACK transitions idempotent
  2 repository     — pending events survive a restart, scoped to their agent
  3 conn. manager  — publish with 0 subscribers is `queued`, never `delivered`
  4 ACK ownership  — only the target agent may set acked_at
  5 heartbeat      — drain returns all un-acked events after failed delivery
  6 background     — fail-closed task never runs run_once on a Redis outage

Steps 1/2/4/5 exercise the REAL V65 migration file against testcontainers
Postgres rather than a re-declared inline schema — a typo in the migration
must fail these tests, which is the whole point of testing a migration.
They are marked @pytest.mark.integration (need Docker).
Steps 3/6 are pure unit tests and need no Docker.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.background import ALL_TASKS, ScheduledTask
from app.repositories.agent_event_repo import AgentEventRepository
from app.services import connection_manager as cm
from app.services.connection_manager import ConnectionManager, DeliveryResult

V65_PATH = Path(__file__).resolve().parents[2] / "db" / "migrations" / "V65__agent_events.sql"

# Minimal FK target only. agent_events DDL comes from the real migration.
AGENTS_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);
"""


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture
async def engine(pg_container):
    """Engine with the agents FK target + the REAL V65 migration applied."""
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    migration_sql = V65_PATH.read_text()
    # One transaction for the whole migration, mirroring Flyway's V__ handling.
    # Statements are applied one at a time because asyncpg refuses multiple
    # commands in a single prepared statement.
    async with eng.begin() as conn:
        for stmt in f"{AGENTS_SCHEMA};{migration_sql}".split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    yield eng
    await eng.dispose()


@pytest.fixture
def session_maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db_session(session_maker):
    async with session_maker() as session:
        yield session


@pytest.fixture
async def agent_id(db_session) -> str:
    aid = str(uuid.uuid4())
    await db_session.execute(
        text("INSERT INTO agents (id, handle, name) VALUES (CAST(:id AS UUID), :h, :n)"),
        {"id": aid, "h": f"fighter-{aid[:8]}", "n": "Fighter"},
    )
    await db_session.commit()
    return aid


@pytest.fixture
async def other_agent_id(db_session) -> str:
    aid = str(uuid.uuid4())
    await db_session.execute(
        text("INSERT INTO agents (id, handle, name) VALUES (CAST(:id AS UUID), :h, :n)"),
        {"id": aid, "h": f"intruder-{aid[:8]}", "n": "Intruder"},
    )
    await db_session.commit()
    return aid


# ── Step 1: migration invariant ───────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_double_ack_is_idempotent_and_leaves_one_row(db_session, agent_id):
    """Insert → deliver → ACK twice → exactly one row, acked_at unchanged."""
    repo = AgentEventRepository(db_session)
    event_id = await repo.create(agent_id, "battle_turn", {"round": 1}, ttl_seconds=600)
    await repo.mark_dispatched(event_id, "delivered")
    await db_session.commit()

    first = await repo.mark_acked(agent_id, [event_id])
    await db_session.commit()
    row1 = (await db_session.execute(
        text("SELECT status, acked_at FROM agent_events WHERE event_id = CAST(:e AS UUID)"),
        {"e": event_id},
    )).mappings().one()

    second = await repo.mark_acked(agent_id, [event_id])
    await db_session.commit()
    row2 = (await db_session.execute(
        text("SELECT status, acked_at FROM agent_events WHERE event_id = CAST(:e AS UUID)"),
        {"e": event_id},
    )).mappings().one()

    assert first == [event_id]
    assert second == []  # second ack transitions nothing
    assert row1["acked_at"] == row2["acked_at"]  # timestamp never moves
    assert row2["status"] == "acked"

    count = (await db_session.execute(
        text("SELECT COUNT(*) FROM agent_events WHERE target_agent_id = CAST(:a AS UUID)"),
        {"a": agent_id},
    )).scalar_one()
    assert count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ack_without_dispatch_cannot_exist(db_session, agent_id):
    """CHECK makes 'acked but never dispatched' unrepresentable."""
    with pytest.raises(Exception, match="agent_events_acked_after_dispatch"):
        await db_session.execute(
            text("""
                INSERT INTO agent_events
                    (target_agent_id, type, payload, status, acked_at, expires_at)
                VALUES (CAST(:a AS UUID), 'battle_turn', '{}'::jsonb, 'acked',
                        NOW(), NOW() + INTERVAL '10 minutes')
            """),
            {"a": agent_id},
        )
    await db_session.rollback()


# ── Step 2: repository invariant ──────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pending_event_survives_session_restart(session_maker, agent_id):
    """Recreate the repository/session and fetch the same pending event."""
    async with session_maker() as s1:
        event_id = await AgentEventRepository(s1).create(
            agent_id, "battle_turn", {"task": "build a thing"}, ttl_seconds=600
        )
        await s1.commit()

    # A brand-new session — nothing carried over in memory.
    async with session_maker() as s2:
        rows = await AgentEventRepository(s2).list_unacked(agent_id)

    assert [str(r["event_id"]) for r in rows] == [event_id]
    assert rows[0]["payload"] == {"task": "build a thing"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_events_are_scoped_to_target_agent(db_session, agent_id, other_agent_id):
    """One agent never sees another agent's pending events."""
    repo = AgentEventRepository(db_session)
    mine = await repo.create(agent_id, "battle_turn", {"n": 1}, ttl_seconds=600)
    await repo.create(other_agent_id, "battle_turn", {"n": 2}, ttl_seconds=600)
    await db_session.commit()

    rows = await repo.list_unacked(agent_id)
    assert [str(r["event_id"]) for r in rows] == [mine]


# ── Step 3: connection manager invariant (no Docker) ──────────────────


@pytest.mark.asyncio
async def test_publish_with_zero_subscribers_is_not_delivery():
    """redis.publish() reporting 0 subscribers must not count as delivered."""
    manager = ConnectionManager()
    redis = AsyncMock()
    redis.publish.return_value = 0  # nobody subscribed
    manager._redis = redis

    assert await manager.send("agent-1", {"type": "battle_turn"}) is False


@pytest.mark.asyncio
async def test_publish_with_a_subscriber_is_delivery():
    """A confirmed subscriber is the only thing that makes a publish delivery."""
    manager = ConnectionManager()
    redis = AsyncMock()
    redis.publish.return_value = 1
    manager._redis = redis

    assert await manager.send("agent-1", {"type": "battle_turn"}) is True


@pytest.mark.asyncio
async def test_deliver_event_returns_queued_when_no_subscribers():
    """0 subscribers + no webhook → deliver_event() returns queued, never delivered."""
    with (
        patch.object(cm, "_persist_durable_event", AsyncMock(return_value="evt-1")),
        patch.object(cm, "_record_dispatch", AsyncMock()) as record,
        patch.object(cm.AgentWebhookService, "deliver", AsyncMock(return_value=False)),
        patch.object(cm, "get_connection_manager") as get_mgr,
    ):
        get_mgr.return_value.send = AsyncMock(return_value=False)
        result = await cm.deliver_event("agent-1", {"type": "battle_turn"})

    assert result == DeliveryResult.QUEUED
    assert result != DeliveryResult.DELIVERED
    record.assert_awaited_once_with("evt-1", "queued")


@pytest.mark.asyncio
async def test_deliver_event_returns_failed_for_non_durable_undeliverable_event():
    """A non-durable type with no receiver is failed — nothing was stored."""
    with (
        patch.object(cm.AgentWebhookService, "deliver", AsyncMock(return_value=False)),
        patch.object(cm, "get_connection_manager") as get_mgr,
    ):
        get_mgr.return_value.send = AsyncMock(return_value=False)
        result = await cm.deliver_event("agent-1", {"type": "flow_step"})

    assert result == DeliveryResult.FAILED


# ── Step 4: ACK ownership invariant ───────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ack_from_wrong_agent_leaves_row_untouched(db_session, agent_id, other_agent_id):
    """A wrong agent's ACK leaves the row unchanged."""
    repo = AgentEventRepository(db_session)
    event_id = await repo.create(agent_id, "battle_turn", {"round": 1}, ttl_seconds=600)
    await repo.mark_dispatched(event_id, "delivered")
    await db_session.commit()

    acked = await repo.mark_acked(other_agent_id, [event_id])
    await db_session.commit()

    row = (await db_session.execute(
        text("SELECT status, acked_at FROM agent_events WHERE event_id = CAST(:e AS UUID)"),
        {"e": event_id},
    )).mappings().one()

    assert acked == []
    assert row["acked_at"] is None
    assert row["status"] == "delivered"  # still awaiting the real target's ack


# ── Step 5: heartbeat drain invariant ─────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_drain_returns_unacked_event_after_failed_delivery(
    db_session, agent_id
):
    """Queue an event (delivery failed), drain it, assert identical id + payload."""
    repo = AgentEventRepository(db_session)
    payload = {"task": "duel", "deadline_seconds": 600}
    event_id = await repo.create(agent_id, "battle_turn", payload, ttl_seconds=600)
    await repo.mark_dispatched(event_id, "queued")  # WS/webhook found nobody
    await db_session.commit()

    rows = await repo.list_unacked(agent_id)

    assert len(rows) == 1
    assert str(rows[0]["event_id"]) == event_id
    assert rows[0]["payload"] == payload

    # Still un-acked → still redelivered on the next heartbeat (at-least-once).
    await repo.mark_drained([event_id])
    await db_session.commit()
    assert len(await repo.list_unacked(agent_id)) == 1

    await repo.mark_acked(agent_id, [event_id])
    await db_session.commit()
    assert await repo.list_unacked(agent_id) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_events_are_not_drained(db_session, agent_id):
    """An event past expires_at is never handed to the agent."""
    await db_session.execute(
        text("""
            INSERT INTO agent_events (target_agent_id, type, payload, status, expires_at)
            VALUES (CAST(:a AS UUID), 'battle_turn', '{}'::jsonb, 'queued',
                    NOW() - INTERVAL '1 minute')
        """),
        {"a": agent_id},
    )
    await db_session.commit()

    assert await AgentEventRepository(db_session).list_unacked(agent_id) == []


# ── Step 6: background leader-lock invariant (no Docker) ──────────────


class _FailClosedTask(ScheduledTask):
    """Stands in for a battle round runner: spends budget, must not double-run."""

    name = "test_fail_closed"
    interval_s = 1
    lock_ttl_s = 10
    fail_closed = True

    def __init__(self) -> None:
        super().__init__()
        self.runs = 0

    async def run_once(self) -> None:
        self.runs += 1


class _FailOpenTask(_FailClosedTask):
    """Counterpart with today's default — proves existing tasks are untouched."""

    name = "test_fail_open"
    fail_closed = False


@pytest.mark.asyncio
async def test_redis_outage_blocks_a_fail_closed_task():
    """Mocked Redis exception → run_once() is never called."""
    task = _FailClosedTask()
    with patch(
        "app.core.background.get_redis", AsyncMock(side_effect=ConnectionError("redis down"))
    ):
        assert await task._acquire_leader() is False
    assert task.runs == 0


@pytest.mark.asyncio
async def test_redis_outage_still_permits_a_fail_open_task():
    """Counterpoint: the existing four tasks keep running during an outage."""
    task = _FailOpenTask()
    with patch(
        "app.core.background.get_redis", AsyncMock(side_effect=ConnectionError("redis down"))
    ):
        assert await task._acquire_leader() is True


@pytest.mark.asyncio
async def test_existing_tasks_are_all_fail_open_by_default():
    """Guard: nothing in ALL_TASKS silently flipped semantics."""
    assert [t.fail_closed for t in ALL_TASKS] == [False] * len(ALL_TASKS)


@pytest.mark.asyncio
async def test_leader_lock_records_token_for_renewal():
    """Acquiring the lease stores a token so renewal can prove ownership."""
    task = _FailClosedTask()
    redis = AsyncMock()
    redis.set.return_value = True
    with patch("app.core.background.get_redis", AsyncMock(return_value=redis)):
        assert await task._acquire_leader() is True

    assert task._lock_token is not None
    # The lease value must be the token, not a constant — otherwise any worker
    # could renew any other worker's lease.
    assert redis.set.await_args.args[1] == task._lock_token
