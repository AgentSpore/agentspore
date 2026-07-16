"""Tests for phase-1 step 8 — challenge, consent, reservation, readiness.

The invariant under test, stated so it can be falsified:

    Opt-in, per-target caps, decline cooldown, ownership and readiness are all
    mandatory, and none can be bypassed. A denied challenge creates NO battle
    row, and a battle reaches 'queued' only on both current-generation
    ready-ACKs from the right agents inside the lease.

THE test is ``test_battle_admission_and_ready_gate_fail_closed``: a capped
target answers 429 and leaves no battle row.

These run the REAL V65 and V66 migrations against testcontainers Postgres. A
mock cannot prove any of this: the admission rules are predicates inside an
INSERT, and the readiness gate is a JOIN against agent_events evaluated at the
transaction timestamp. Mocking either would only prove that a mock returns what
it was told to.

Note on what is deliberately NOT asserted here: that deliver_event was called.
A DELIVERED result is not readiness (fact 2 vs fact 4), so a test that asserts
delivery would be asserting the very confusion this step exists to prevent.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.api.v1.battles import _DENIAL_STATUS
from app.repositories.battle_repo import BattleRepository, ChallengeDenial
from app.schemas.battles import BattleStatus, TaskSource
from app.services import battle_service as battle_service_module
from app.services.battle_service import (
    TARGET_CHALLENGE_CAP,
    TARGET_CHALLENGE_WINDOW_SECONDS,
    BattleService,
    ChallengeDeniedError,
    LimiterUnavailableError,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"

RUBRIC = [{"criterion": "correctness", "weight": 1.0}]

# Minimal FK targets. The battle tables' DDL, and every column V66 adds to
# agents (available_for_battles included), come from the real migration.
BASE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_hosted BOOLEAN NOT NULL DEFAULT FALSE,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class _FakeRedis:
    """Counts like Redis does. The limiter only needs INCR + EXPIRE."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None


@pytest.fixture(autouse=True)
def redis_up(monkeypatch):
    """A working limiter by default. Subcases that need an outage override it."""
    fake = _FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(battle_service_module, "get_redis", _get_redis)
    return fake


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    """FK targets + the REAL V65 and V66 migrations, applied exactly once."""
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()}"
    async with eng.begin() as conn:
        for stmt in sql.split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    yield eng
    await eng.dispose()


@pytest.fixture(scope="module")
def session_maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="module")
async def db(session_maker):
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def owner_id(db) -> str:
    uid = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"owner-{uid[:8]}@example.test"},
    )
    await db.commit()
    return uid


@pytest_asyncio.fixture(loop_scope="module")
async def make_agent(db, owner_id):
    """Mint an agent. Opted in and eligible unless a subcase says otherwise."""

    async def _make(
        *,
        available: bool = True,
        is_active: bool = True,
        is_hosted: bool = False,
        owner: str | None = None,
    ) -> str:
        aid = str(uuid.uuid4())
        await db.execute(
            text(
                """
                INSERT INTO agents
                    (id, handle, name, is_active, is_hosted, owner_user_id,
                     available_for_battles)
                VALUES (CAST(:id AS UUID), :h, 'Fighter', :active, :hosted,
                        CAST(:owner AS UUID), :available)
                """
            ),
            {
                "id": aid,
                "h": f"fighter-{aid[:8]}",
                "active": is_active,
                "hosted": is_hosted,
                "owner": owner if owner is not None else owner_id,
                "available": available,
            },
        )
        await db.commit()
        return aid

    return _make


@pytest_asyncio.fixture(loop_scope="module")
async def task_id(db, owner_id) -> str:
    tid = await BattleRepository(db).create_task(
        source=TaskSource.GENERATED,
        title="Write a parser",
        prompt="Parse this log format.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        created_by_user_id=owner_id,
    )
    await db.commit()
    return tid


async def _count_battles(db, agent_b_id: str) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM battles "
                    "WHERE agent_b_id = CAST(:id AS UUID)"
                ),
                {"id": agent_b_id},
            )
        ).scalar()
    )


# ── THE test ────────────────────────────────────────────────────────────────


async def test_battle_admission_and_ready_gate_fail_closed(
    db, owner_id, task_id, make_agent
):
    """A capped target answers 429 and NO battle row is created.

    The cap is filled by five DIFFERENT challengers, because the one-per-pair
    rule already stops a single challenger from stacking five. That is the
    shape the cap actually has to survive: a per-challenger limiter (the
    councils one, 10/hour) would happily let ten accounts each land one
    challenge on the same target and call it compliant.
    """
    target = await make_agent()
    svc = BattleService(db)

    for _ in range(TARGET_CHALLENGE_CAP):
        challenger = await make_agent()
        await svc.create_challenge(
            task_id=task_id,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
        await db.commit()

    assert await _count_battles(db, target) == TARGET_CHALLENGE_CAP
    before = await _count_battles(db, target)

    over_cap = await make_agent()
    with pytest.raises(ChallengeDeniedError) as denied:
        await svc.create_challenge(
            task_id=task_id,
            agent_a_id=over_cap,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()

    assert denied.value.reason is ChallengeDenial.TARGET_CAPPED
    # The invariant's second half: the denial left nothing behind.
    assert await _count_battles(db, target) == before
    # And the first half — the API answers 429, not a generic refusal.
    assert _DENIAL_STATUS[ChallengeDenial.TARGET_CAPPED] == 429

    # Everything above only proves the DIAGNOSTIC refused: it raises before the
    # INSERT is ever reached, so it cannot tell us the gate works. The gate is
    # the predicate inside create_challenge, and this is what exercises it —
    # the path taken by any caller that skips diagnose_challenge, and the only
    # thing standing between a capped target and a battle row.
    direct = await svc.repo.create_challenge(
        task_id=task_id,
        agent_a_id=over_cap,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=60,
        target_cap=TARGET_CHALLENGE_CAP,
        target_window_seconds=TARGET_CHALLENGE_WINDOW_SECONDS,
        agent_b_id=target,
        agent_b_owner_snapshot=owner_id,
    )
    assert direct is None
    await db.rollback()
    assert await _count_battles(db, target) == before


# ── admission subcases ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "agent_kwargs,expected",
    [
        ({"available": False}, ChallengeDenial.TARGET_INELIGIBLE),
        ({"is_active": False}, ChallengeDenial.TARGET_INELIGIBLE),
        ({"is_hosted": True}, ChallengeDenial.TARGET_INELIGIBLE),
    ],
)
async def test_ineligible_target_is_denied_and_creates_no_row(
    db, owner_id, task_id, make_agent, agent_kwargs, expected
):
    """Opt-out, deactivation and hosted-ness each block a challenge."""
    target = await make_agent(**agent_kwargs)
    challenger = await make_agent()

    with pytest.raises(ChallengeDeniedError) as denied:
        await BattleService(db).create_challenge(
            task_id=task_id,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied.value.reason is expected
    assert await _count_battles(db, target) == 0


async def test_decline_starts_cooldown_that_blocks_the_next_challenge(
    db, owner_id, task_id, make_agent
):
    """Decline is not advisory: the same challenger cannot immediately re-ask."""
    target = await make_agent()
    challenger = await make_agent()
    svc = BattleService(db)

    battle_id = await svc.create_challenge(
        task_id=task_id,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    assert await svc.decline(battle_id) is not None
    await db.commit()

    with pytest.raises(ChallengeDeniedError) as denied:
        await svc.create_challenge(
            task_id=task_id,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied.value.reason is ChallengeDenial.COOLING_DOWN
    assert await _count_battles(db, target) == 1  # the declined one only


async def test_block_list_denies_and_creates_no_row(
    db, owner_id, task_id, make_agent
):
    """A blocked challenger cannot reach the target at all."""
    target = await make_agent()
    challenger = await make_agent()
    await db.execute(
        text(
            "INSERT INTO battle_blocks (blocker_agent_id, blocked_agent_id) "
            "VALUES (CAST(:t AS UUID), CAST(:c AS UUID))"
        ),
        {"t": target, "c": challenger},
    )
    await db.commit()

    with pytest.raises(ChallengeDeniedError) as denied:
        await BattleService(db).create_challenge(
            task_id=task_id,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied.value.reason is ChallengeDenial.BLOCKED
    assert await _count_battles(db, target) == 0


async def test_limiter_outage_creates_no_battle_row(
    db, owner_id, task_id, make_agent, monkeypatch
):
    """Redis down = deny. A limiter we cannot consult must not wave it through.

    This is the subcase that separates this limiter from the councils one: the
    same outage there logs and continues, because a council spends the
    platform's own credits. Here it spends the target owner's.
    """
    target = await make_agent()
    challenger = await make_agent()

    async def _boom():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(battle_service_module, "get_redis", _boom)

    with pytest.raises(LimiterUnavailableError):
        await BattleService(db).create_challenge(
            task_id=task_id,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert await _count_battles(db, target) == 0


async def test_one_pending_challenge_per_pair(db, owner_id, task_id, make_agent):
    """A pair may have one live battle, so a challenger cannot stack them."""
    target = await make_agent()
    challenger = await make_agent()
    svc = BattleService(db)

    await svc.create_challenge(
        task_id=task_id,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    with pytest.raises(ChallengeDeniedError) as denied:
        await svc.create_challenge(
            task_id=task_id,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied.value.reason is ChallengeDenial.PAIR_ALREADY_ENGAGED
    assert await _count_battles(db, target) == 1


# ── readiness subcases ──────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="module")
async def armed_battle(db, owner_id, task_id, make_agent):
    """A battle reserved and armed for readiness, with both event ids."""
    agent_a = await make_agent()
    agent_b = await make_agent()
    svc = BattleService(db)

    battle_id = await svc.create_challenge(
        task_id=task_id,
        agent_a_id=agent_a,
        challenger_owner_user_id=owner_id,
        agent_b_id=agent_b,
    )
    await db.commit()
    assert await svc.accept(battle_id) is not None
    await db.commit()

    armed = await svc.arm_readiness(battle_id)
    await db.commit()
    assert armed is not None
    assert armed["status"] == BattleStatus.RESERVED.value
    return armed


async def test_consent_alone_never_queues_a_battle(db, armed_battle):
    """Owner consent is fact 1. It is not readiness, and it never queues."""
    svc = BattleService(db)
    assert armed_battle["agent_b_accepted_at"] is not None
    queued = await svc.try_queue(
        str(armed_battle["id"]), armed_battle["readiness_generation"]
    )
    assert queued is None


async def test_one_missing_ack_never_queues(db, armed_battle):
    """One side ready is not both sides ready."""
    svc = BattleService(db)
    await svc.events.mark_acked(
        str(armed_battle["agent_a_id"]),
        [str(armed_battle["ready_check_event_id_a"])],
    )
    await db.commit()

    assert (
        await svc.try_queue(
            str(armed_battle["id"]), armed_battle["readiness_generation"]
        )
        is None
    )


async def test_wrong_agent_ack_never_queues(db, armed_battle):
    """B cannot ack A's event: mark_acked is scoped to the target agent."""
    svc = BattleService(db)
    acked = await svc.events.mark_acked(
        str(armed_battle["agent_b_id"]),
        [str(armed_battle["ready_check_event_id_a"])],
    )
    await db.commit()
    assert acked == []
    assert (
        await svc.try_queue(
            str(armed_battle["id"]), armed_battle["readiness_generation"]
        )
        is None
    )


async def test_expired_lease_never_queues_and_releases_both(db, armed_battle):
    """A lapsed lease releases BOTH fighters and queues nothing."""
    svc = BattleService(db)
    battle_id = str(armed_battle["id"])
    for side, agent_key in (("a", "agent_a_id"), ("b", "agent_b_id")):
        await svc.events.mark_acked(
            str(armed_battle[agent_key]),
            [str(armed_battle[f"ready_check_event_id_{side}"])],
        )
    await db.commit()

    # Force the lease into the past: both ACKs are in, but they are now stale
    # evidence. The gate must refuse anyway.
    await db.execute(
        text(
            "UPDATE battles SET ready_lease_expires_at = NOW() - interval '1 second' "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"id": battle_id},
    )
    await db.commit()

    assert await svc.try_queue(battle_id, armed_battle["readiness_generation"]) is None

    released = await svc.release_expired_readiness(battle_id)
    await db.commit()
    assert released is not None
    assert released["status"] == BattleStatus.ACCEPTED.value
    remaining = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM battle_reservations "
                    "WHERE battle_id = CAST(:id AS UUID)"
                ),
                {"id": battle_id},
            )
        ).scalar()
    )
    assert remaining == 0  # both, not one


async def test_stale_generation_ack_never_queues(db, armed_battle):
    """ACKs from a previous arming cannot satisfy the current generation."""
    svc = BattleService(db)
    battle_id = str(armed_battle["id"])
    old_generation = armed_battle["readiness_generation"]

    for side, agent_key in (("a", "agent_a_id"), ("b", "agent_b_id")):
        await svc.events.mark_acked(
            str(armed_battle[agent_key]),
            [str(armed_battle[f"ready_check_event_id_{side}"])],
        )
    await db.commit()

    # Lapse and release, then re-arm: a NEW generation with NEW event ids.
    await db.execute(
        text(
            "UPDATE battles SET ready_lease_expires_at = NOW() - interval '1 second' "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"id": battle_id},
    )
    await db.commit()
    assert await svc.release_expired_readiness(battle_id) is not None
    await db.commit()

    rearmed = await svc.arm_readiness(battle_id)
    await db.commit()
    assert rearmed is not None
    assert rearmed["readiness_generation"] > old_generation

    # The old generation is gone.
    assert await svc.try_queue(battle_id, old_generation) is None
    # And the new one is not satisfied by the ACKs of the old events.
    assert await svc.try_queue(battle_id, rearmed["readiness_generation"]) is None


async def test_both_current_acks_queue_the_battle(db, armed_battle):
    """The one path in: both current-generation ACKs, from the right agents."""
    svc = BattleService(db)
    battle_id = str(armed_battle["id"])
    for side, agent_key in (("a", "agent_a_id"), ("b", "agent_b_id")):
        acked = await svc.events.mark_acked(
            str(armed_battle[agent_key]),
            [str(armed_battle[f"ready_check_event_id_{side}"])],
        )
        assert len(acked) == 1
    await db.commit()

    queued = await svc.try_queue(battle_id, armed_battle["readiness_generation"])
    await db.commit()
    assert queued is not None
    assert queued["status"] == BattleStatus.QUEUED.value
    assert queued["queued_at"] is not None
