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
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.api.deps import get_current_user, get_optional_user
from app.api.v1.battles import _DENIAL_STATUS, _optional_fighter
from app.core.database import get_db
from app.main import app
from app.repositories.agent_repo import AgentRepository
from app.repositories.battle_repo import BattleRepository, ChallengeDenial
from app.schemas.battles import BattleStatus, JudgeKind, PresentedOrder, Side, TaskSource, Vote
from app.services import battle_service as battle_service_module
from app.services import connection_manager as cm
from app.services.battle_service import (
    CHALLENGER_RATE_LIMIT,
    TARGET_CHALLENGE_CAP,
    TARGET_CHALLENGE_WINDOW_SECONDS,
    BattleService,
    ChallengeDeniedError,
    LimiterUnavailableError,
)

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"
V67_PATH = MIGRATIONS / "V67__battle_task_secrecy.sql"

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
    sql = f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()};{V67_PATH.read_text()}"
    async with eng.begin() as conn:
        for stmt in split_sql_statements(sql):
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
    # V67: a challenge over an "any" filter needs a fresh pool of at least
    # MINIMUM_TASK_POOL (20). Seed a full pool of IDENTICAL general/medium tasks
    # so admission passes and any bound task yields the same snapshot; return one
    # representative id for the transition-test helpers.
    repo = BattleRepository(db)
    tid = await repo.create_task(
        source=TaskSource.GENERATED,
        category="general",
        title="Write a parser",
        prompt="Parse this log format.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        created_by_user_id=owner_id,
    )
    for _ in range(24):
        await repo.create_task(
            source=TaskSource.GENERATED,
            category="general",
            title="Write a parser",
            prompt="Parse this log format.",
            rubric=RUBRIC,
            time_limit_seconds=600,
            created_by_user_id=owner_id,
        )
    await db.commit()
    return tid


async def _try_queue(svc, db, battle_id, generation):
    """try_queue with a freshly stamped bind lease (V67 binding is lease-fenced).

    admit_to_queue now requires the row's lease_token + a live lease, so a test
    that queues a reserved battle must first claim it the way the reconciler's
    reserved phase does. Stamped in the SAME session so it is visible to the
    binding transaction without a commit; the binding clears it on success.
    """
    token = str(uuid.uuid4())
    await db.execute(
        text(
            "UPDATE battles SET lease_token = CAST(:t AS UUID), "
            "lease_expires_at = NOW() + make_interval(secs => 15) "
            "WHERE id = CAST(:b AS UUID)"
        ),
        {"t": token, "b": str(battle_id)},
    )
    return await svc.try_queue(str(battle_id), generation, token)


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
            task_category=None,
        task_difficulty=None,
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
            task_category=None,
        task_difficulty=None,
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
        task_category=None,
        task_difficulty=None,
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
            task_category=None,
        task_difficulty=None,
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
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    assert await svc.decline(battle_id, owner_id) is not None
    await db.commit()

    with pytest.raises(ChallengeDeniedError) as denied:
        await svc.create_challenge(
            task_category=None,
        task_difficulty=None,
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
            task_category=None,
        task_difficulty=None,
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
            task_category=None,
        task_difficulty=None,
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
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    with pytest.raises(ChallengeDeniedError) as denied:
        await svc.create_challenge(
            task_category=None,
        task_difficulty=None,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied.value.reason is ChallengeDenial.PAIR_ALREADY_ENGAGED
    assert await _count_battles(db, target) == 1


async def test_challenger_rate_limit_names_the_challenger_not_the_target(
    db, owner_id, task_id, make_agent, redis_up
):
    """The caller's own quota must not be reported as the target being full.

    Telling an owner "the target has reached its limit" when the target is
    nowhere near it sends them to look at somebody else's state for a problem
    that is theirs. Both answer 429; only one of them is true.
    """
    challenger = await make_agent()
    redis_up.counters[f"battle:challenge:ratelimit:{challenger}"] = (
        CHALLENGER_RATE_LIMIT
    )

    with pytest.raises(ChallengeDeniedError) as denied:
        await BattleService(db).create_challenge(
            task_category=None,
        task_difficulty=None,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=await make_agent(),
        )
    await db.rollback()
    assert denied.value.reason is ChallengeDenial.CHALLENGER_RATE_LIMITED
    assert _DENIAL_STATUS[ChallengeDenial.CHALLENGER_RATE_LIMITED] == 429


async def test_list_battles_filters_by_status_and_lists_unfiltered(
    db, owner_id, task_id, make_agent
):
    """Both branches of the split status filter return the right rows.

    list_battles emits two different statements now — the sargable rewrite that
    lets a status index be usable at all — so both need exercising. A one-liner
    OR-predicate could never take a wrong branch; two statements can.
    """
    repo = BattleRepository(db)
    target = await make_agent()
    challenger = await make_agent()
    battle_id = await BattleService(db).create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    pending = await repo.list_battles(status=BattleStatus.CHALLENGE_PENDING, limit=100)
    assert battle_id in {str(b["id"]) for b in pending}
    assert {b["status"] for b in pending} == {BattleStatus.CHALLENGE_PENDING.value}

    # The other branch: no WHERE clause at all.
    unfiltered = await repo.list_battles(limit=100)
    assert battle_id in {str(b["id"]) for b in unfiltered}

    # A status this battle is not in must not return it.
    completed = await repo.list_battles(status=BattleStatus.COMPLETED, limit=100)
    assert battle_id not in {str(b["id"]) for b in completed}


# ── opt-in: the flag has to be reachable, or the feature does not exist ─────


async def test_owner_can_opt_in_and_out_and_the_gate_follows(
    db, owner_id, task_id, make_agent
):
    """The toggle is the whole feature: default is FALSE, so without a way to
    set it no agent can ever be challenged.

    Opting out must be visible to the admission gate immediately — a flag the
    gate ignores would be worse than no flag, since the owner would believe
    they had said no.
    """
    repo = AgentRepository(db)
    target = await make_agent(available=False)
    challenger = await make_agent()
    svc = BattleService(db)

    # Opted out (the DEFAULT state of every agent): challenge is refused.
    with pytest.raises(ChallengeDeniedError) as denied:
        await svc.create_challenge(
            task_category=None,
        task_difficulty=None,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied.value.reason is ChallengeDenial.TARGET_INELIGIBLE

    # Owner opts in → the same challenge is now admissible.
    assert await repo.set_battle_availability(target, owner_id, True) is True
    await db.commit()
    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()
    assert battle_id is not None

    # Owner opts back out → refused again, and the gate says why.
    assert await repo.set_battle_availability(target, owner_id, False) is True
    await db.commit()
    other = await make_agent()
    with pytest.raises(ChallengeDeniedError) as denied_again:
        await svc.create_challenge(
            task_category=None,
        task_difficulty=None,
            agent_a_id=other,
            challenger_owner_user_id=owner_id,
            agent_b_id=target,
        )
    await db.rollback()
    assert denied_again.value.reason is ChallengeDenial.TARGET_INELIGIBLE


async def test_opting_out_does_not_disturb_a_battle_already_under_way(
    db, owner_id, task_id, make_agent
):
    """Opting out governs FUTURE challenges; it does not cancel a live battle.

    The snapshots and both owners' consent are already fixed. Cascading a
    toggle into running battles would let one owner destroy work the other
    agreed to — and it is unnecessary, because admit_to_queue re-checks
    eligibility at the transition and will refuse on its own.
    """
    repo = AgentRepository(db)
    target = await make_agent()
    challenger = await make_agent()
    svc = BattleService(db)
    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()
    assert await svc.accept(battle_id, owner_id) is not None
    await db.commit()

    assert await repo.set_battle_availability(target, owner_id, False) is True
    await db.commit()

    battle = await BattleRepository(db).get(battle_id)
    assert battle["status"] == BattleStatus.ACCEPTED.value  # untouched
    assert battle["agent_b_accepted_at"] is not None


async def test_only_the_owner_can_toggle_battle_availability(
    db, owner_id, make_agent
):
    """A stranger cannot volunteer someone else's agent — or its owner's money."""
    repo = AgentRepository(db)
    agent = await make_agent(available=False)
    stranger = str(uuid.uuid4())

    assert await repo.set_battle_availability(agent, stranger, True) is False
    await db.rollback()

    still_off = (
        await db.execute(
            text(
                "SELECT available_for_battles FROM agents WHERE id = CAST(:a AS UUID)"
            ),
            {"a": agent},
        )
    ).scalar()
    assert still_off is False


# ── consent: ownership is proven by the write, not by an earlier read ────────


async def test_accept_by_a_user_who_no_longer_owns_the_agent_is_refused(
    db, owner_id, task_id, make_agent
):
    """Consent must be written by the CURRENT owner, not a stale reader.

    The live race: the router reads owner_user_id = U1 and passes, ownership.py
    links the agent to U2 and commits, and the consent that authorises spending
    U2's money is then recorded on U1's say-so. Sessions are READ COMMITTED, so
    the router's read is stale the instant it lands — only the CAS can decide.
    """
    agent_a = await make_agent()
    agent_b = await make_agent()
    svc = BattleService(db)

    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=agent_a,
        challenger_owner_user_id=owner_id,
        agent_b_id=agent_b,
    )
    await db.commit()

    # The handover the router cannot see: agent B now belongs to someone else.
    new_owner = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": new_owner, "e": f"new-{new_owner[:8]}@example.test"},
    )
    await db.execute(
        text(
            "UPDATE agents SET owner_user_id = CAST(:u AS UUID) "
            "WHERE id = CAST(:a AS UUID)"
        ),
        {"u": new_owner, "a": agent_b},
    )
    await db.commit()

    # The old owner may no longer consent...
    assert await svc.accept(battle_id, owner_id) is None
    await db.rollback()
    # ...and neither may the new one: the snapshot that decides rating still
    # names the old owner, so nobody in this battle agreed to fight this one.
    assert await svc.accept(battle_id, new_owner) is None
    await db.rollback()

    battle = await BattleRepository(db).get(battle_id)
    assert battle["agent_b_accepted_at"] is None
    assert battle["status"] == BattleStatus.CHALLENGE_PENDING.value


async def test_decline_by_a_user_who_does_not_own_the_agent_is_refused(
    db, owner_id, task_id, make_agent
):
    """A decline is not harmless just because it spends nobody's inference.

    It kills a battle the real owner may have wanted AND stamps a 24h cooldown
    on the challenger — so an unauthorised decline damages a third party's
    standing too. Same read-then-write race as accept had; same fix.
    """
    target = await make_agent()
    challenger = await make_agent()
    svc = BattleService(db)
    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    stranger = str(uuid.uuid4())
    assert await svc.decline(battle_id, stranger) is None
    await db.rollback()

    battle = await BattleRepository(db).get(battle_id)
    assert battle["status"] == BattleStatus.CHALLENGE_PENDING.value
    # ...and no cooldown was stamped on the challenger by the stranger.
    cooldowns = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM battle_challenge_cooldowns "
                "WHERE challenger_agent_id = CAST(:c AS UUID)"
            ),
            {"c": challenger},
        )
    ).scalar()
    assert cooldowns == 0


async def test_decline_still_works_for_an_ineligible_agent(
    db, owner_id, task_id, make_agent
):
    """Saying no must never require being eligible to fight.

    Unlike accept, decline deliberately does not re-check eligibility: if it
    did, an agent deactivated or opted out after being challenged could neither
    accept nor decline, and the challenge would sit until it expired. Only
    saying YES requires eligibility.
    """
    target = await make_agent()
    challenger = await make_agent()
    svc = BattleService(db)
    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()

    await db.execute(
        text(
            "UPDATE agents SET is_active = FALSE, available_for_battles = FALSE "
            "WHERE id = CAST(:a AS UUID)"
        ),
        {"a": target},
    )
    await db.commit()

    declined = await svc.decline(battle_id, owner_id)
    await db.commit()
    assert declined is not None
    assert declined["status"] == BattleStatus.DECLINED.value


async def test_accept_by_a_stranger_is_refused(db, owner_id, task_id, make_agent):
    """A user who never owned B cannot consent on B's behalf."""
    agent_a = await make_agent()
    agent_b = await make_agent()
    svc = BattleService(db)
    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=agent_a,
        challenger_owner_user_id=owner_id,
        agent_b_id=agent_b,
    )
    await db.commit()

    stranger = str(uuid.uuid4())
    assert await svc.accept(battle_id, stranger) is None
    await db.rollback()
    assert (await BattleRepository(db).get(battle_id))["agent_b_accepted_at"] is None


# ── open challenge: the claimant passes the same rules as a named one ───────


@pytest_asyncio.fixture(loop_scope="module")
async def open_challenge(db, owner_id, task_id, make_agent):
    """An open challenge (agent_b_id NULL) plus the agent that issued it."""

    async def _open() -> tuple[str, str]:
        challenger = await make_agent()
        battle_id = await BattleService(db).create_challenge(
            task_category=None,
        task_difficulty=None,
            agent_a_id=challenger,
            challenger_owner_user_id=owner_id,
            agent_b_id=None,
        )
        await db.commit()
        return battle_id, challenger

    return _open


async def test_claiming_an_open_challenge_fills_the_slot_without_consenting(
    db, owner_id, open_challenge, make_agent
):
    """The happy path — and claiming is still not consent."""
    battle_id, challenger = await open_challenge()
    claimant = await make_agent()

    claimed = await BattleService(db).claim_open_challenge(
        battle_id, claimant, owner_id
    )
    await db.commit()
    assert claimed is not None
    assert str(claimed["agent_b_id"]) == claimant
    # Still pending, still unaccepted: filling the slot is not agreeing to fight.
    assert claimed["status"] == BattleStatus.CHALLENGE_PENDING.value
    assert claimed["agent_b_accepted_at"] is None


async def test_a_blocked_agent_cannot_claim_an_open_challenge(
    db, owner_id, open_challenge, make_agent
):
    """The bypass this guard exists for: challenge nobody, wait for the blocked.

    Blocks are checked in BOTH directions, so this must hold whichever side
    did the blocking.
    """
    for blocker_is_claimant in (True, False):
        battle_id, challenger = await open_challenge()
        claimant = await make_agent()
        blocker, blocked = (
            (claimant, challenger) if blocker_is_claimant else (challenger, claimant)
        )
        await db.execute(
            text(
                "INSERT INTO battle_blocks (blocker_agent_id, blocked_agent_id) "
                "VALUES (CAST(:x AS UUID), CAST(:y AS UUID))"
            ),
            {"x": blocker, "y": blocked},
        )
        await db.commit()

        assert (
            await BattleService(db).claim_open_challenge(battle_id, claimant, owner_id)
            is None
        ), f"blocked claim succeeded (blocker_is_claimant={blocker_is_claimant})"
        await db.rollback()

        battle = await BattleRepository(db).get(battle_id)
        assert battle["agent_b_id"] is None  # slot still empty


async def test_an_opted_out_agent_cannot_claim_an_open_challenge(
    db, owner_id, open_challenge, make_agent
):
    """An open challenge is not a way around the opt-in."""
    battle_id, _ = await open_challenge()
    claimant = await make_agent(available=False)

    assert await BattleService(db).claim_open_challenge(battle_id, claimant, owner_id) is None
    await db.rollback()
    assert (await BattleRepository(db).get(battle_id))["agent_b_id"] is None


async def test_a_stranger_cannot_claim_with_an_agent_they_do_not_own(
    db, owner_id, open_challenge, make_agent
):
    """Ownership of the claiming agent is proven by the write."""
    battle_id, _ = await open_challenge()
    claimant = await make_agent()

    assert (
        await BattleService(db).claim_open_challenge(
            battle_id, claimant, str(uuid.uuid4())
        )
        is None
    )
    await db.rollback()
    assert (await BattleRepository(db).get(battle_id))["agent_b_id"] is None


async def test_an_agent_cannot_claim_its_own_open_challenge(
    db, owner_id, open_challenge
):
    """Self-claim is refused at the gate, not left to the CHECK constraint.

    battle_distinct_agents would catch it, but as an IntegrityError — a 500
    dressed as a bug. A 409 is the honest answer to "you cannot fight yourself".
    """
    battle_id, challenger = await open_challenge()

    assert (
        await BattleService(db).claim_open_challenge(battle_id, challenger, owner_id)
        is None
    )
    await db.rollback()
    assert (await BattleRepository(db).get(battle_id))["agent_b_id"] is None


async def test_only_one_claimant_wins_an_open_challenge(
    db, owner_id, open_challenge, make_agent
):
    """The slot is filled once: a second claimant finds it gone."""
    battle_id, _ = await open_challenge()
    first = await make_agent()
    second = await make_agent()
    svc = BattleService(db)

    assert await svc.claim_open_challenge(battle_id, first, owner_id) is not None
    await db.commit()
    assert await svc.claim_open_challenge(battle_id, second, owner_id) is None
    await db.rollback()

    battle = await BattleRepository(db).get(battle_id)
    assert str(battle["agent_b_id"]) == first


# ── readiness subcases ──────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="module")
async def armed_battle(db, owner_id, task_id, make_agent):
    """A battle reserved and armed for readiness, with both event ids."""
    agent_a = await make_agent()
    agent_b = await make_agent()
    svc = BattleService(db)

    battle_id = await svc.create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=agent_a,
        challenger_owner_user_id=owner_id,
        agent_b_id=agent_b,
    )
    await db.commit()
    assert await svc.accept(battle_id, owner_id) is not None
    await db.commit()

    armed = await svc.arm_readiness(battle_id)
    await db.commit()
    assert armed is not None
    assert armed["status"] == BattleStatus.RESERVED.value
    return armed


async def test_dispatching_ready_checks_leaves_exactly_one_event_per_fighter(
    db, armed_battle, session_maker
):
    """Arming persists the rows; dispatch must send them, not mint more.

    deliver_event inserts unconditionally for a durable type, so dispatching
    through it gave each fighter TWO battle_ready_check rows: the armed one
    readiness is bound to, and a duplicate. The duplicate is worse than noise —
    an agent that acks it never becomes ready, because both_sides_ready joins
    the exact armed ids, so the battle stalls until its lease lapses.
    """
    sent: list[tuple[str, str]] = []

    async def _record_send(agent_id, event):
        sent.append((str(agent_id), event["event_id"]))
        return True

    # The REAL dispatch_existing runs — stubbing it would prove nothing about
    # whether it inserts. Only its transport and its session are replaced.
    with (
        patch.object(cm, "async_session_maker", session_maker),
        patch.object(cm, "get_connection_manager") as get_mgr,
    ):
        get_mgr.return_value.send = _record_send
        results = await BattleService(db).dispatch_ready_checks(armed_battle)
    await db.commit()

    assert set(results) == {"a", "b"}
    for agent_key in ("agent_a_id", "agent_b_id"):
        rows = (
            await db.execute(
                text(
                    "SELECT event_id FROM agent_events "
                    "WHERE target_agent_id = CAST(:a AS UUID) "
                    "AND type = 'battle_ready_check'"
                ),
                {"a": str(armed_battle[agent_key])},
            )
        ).fetchall()
        assert len(rows) == 1, f"{agent_key} got {len(rows)} rows, expected exactly 1"

    # And the one row per side is the ARMED id — the one readiness is bound to.
    assert {e for _, e in sent} == {
        str(armed_battle["ready_check_event_id_a"]),
        str(armed_battle["ready_check_event_id_b"]),
    }


async def test_consent_alone_never_queues_a_battle(db, armed_battle):
    """Owner consent is fact 1. It is not readiness, and it never queues."""
    svc = BattleService(db)
    assert armed_battle["agent_b_accepted_at"] is not None
    queued = await _try_queue(svc, db,
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
        await _try_queue(svc, db,
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
        await _try_queue(svc, db,
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

    assert await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"]) is None

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
    assert await _try_queue(svc, db, battle_id, old_generation) is None
    # And the new one is not satisfied by the ACKs of the old events.
    assert await _try_queue(svc, db, battle_id, rearmed["readiness_generation"]) is None


async def _ack_both(svc, armed_battle) -> None:
    """Both sides ready-ACK the current generation. The happy precondition."""
    for side, agent_key in (("a", "agent_a_id"), ("b", "agent_b_id")):
        acked = await svc.events.mark_acked(
            str(armed_battle[agent_key]),
            [str(armed_battle[f"ready_check_event_id_{side}"])],
        )
        assert len(acked) == 1


async def test_reaped_reservations_never_queue_the_battle(db, armed_battle):
    """Ready-ACKs are not enough: the battle must still HOLD both fighters.

    delete_expired_reservations() reaps on wall-clock time alone and consults no
    battle. With RESERVATION_SECONDS=90 and READY_LEASE_SECONDS=60 a battle can
    be admissible on ACKs while holding nothing, and an unguarded queue would
    let it start while both fighters are free for another battle — the exact
    double-spend of both owners' keys reservations exist to prevent.
    """
    svc = BattleService(db)
    battle_id = str(armed_battle["id"])
    await _ack_both(svc, armed_battle)
    await db.commit()

    freed = await BattleRepository(db).release_reservations(battle_id)
    await db.commit()
    assert len(freed) == 2

    assert await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"]) is None


async def test_owner_change_after_ack_never_queues(db, armed_battle):
    """A fighter that changed hands after acking must not start.

    The snapshots decide rating and reward, so a battle whose fighter now
    belongs to someone else is a battle between parties who never both agreed.
    """
    svc = BattleService(db)
    battle_id = str(armed_battle["id"])
    await _ack_both(svc, armed_battle)

    new_owner = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": new_owner, "e": f"new-{new_owner[:8]}@example.test"},
    )
    await db.execute(
        text(
            "UPDATE agents SET owner_user_id = CAST(:u AS UUID) "
            "WHERE id = CAST(:a AS UUID)"
        ),
        {"u": new_owner, "a": str(armed_battle["agent_b_id"])},
    )
    await db.commit()

    assert await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"]) is None


async def test_deactivated_fighter_after_ack_never_queues(db, armed_battle):
    """is_active goes FALSE as a SIDE EFFECT of revoking OAuth.

    revoke_github_oauth (agent_repo.py:140) deactivates the agent; nothing in
    that path knows battles exist. An agent therefore drops out of eligibility
    silently, and only a re-check at the consequential transition can notice.
    """
    svc = BattleService(db)
    battle_id = str(armed_battle["id"])
    await _ack_both(svc, armed_battle)
    await db.execute(
        text(
            "UPDATE agents SET is_active = FALSE WHERE id = CAST(:a AS UUID)"
        ),
        {"a": str(armed_battle["agent_a_id"])},
    )
    await db.commit()

    assert await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"]) is None


async def test_start_refuses_a_battle_holding_no_reservations(db, armed_battle):
    """The start re-proves the holds; the queue's verdict is not inherited.

    An arbitrary interval passes between queueing and starting, and the reaper
    runs on wall-clock time throughout it. Starting is when the money is spent.
    """
    svc = BattleService(db)
    repo = BattleRepository(db)
    battle_id = str(armed_battle["id"])
    await _ack_both(svc, armed_battle)
    await db.commit()

    queued = await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"])
    await db.commit()
    assert queued is not None

    # The reaper catches up between queue and start.
    assert len(await repo.release_reservations(battle_id)) == 2
    await db.commit()

    started = await repo.start_if_still_eligible(
        battle_id=battle_id, lease_token=str(uuid.uuid4()), lease_seconds=60
    )
    assert started is None
    await db.rollback()
    assert (await repo.get(battle_id))["status"] == BattleStatus.QUEUED.value


async def test_start_refuses_a_deactivated_fighter(db, armed_battle):
    """Eligibility is re-proven at the start, not inherited from the queue."""
    svc = BattleService(db)
    repo = BattleRepository(db)
    battle_id = str(armed_battle["id"])
    await _ack_both(svc, armed_battle)
    await db.commit()

    assert await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"])
    await db.commit()

    await db.execute(
        text("UPDATE agents SET is_active = FALSE WHERE id = CAST(:a AS UUID)"),
        {"a": str(armed_battle["agent_b_id"])},
    )
    await db.commit()

    assert (
        await repo.start_if_still_eligible(
            battle_id=battle_id, lease_token=str(uuid.uuid4()), lease_seconds=60
        )
        is None
    )
    await db.rollback()


async def test_start_succeeds_when_everything_still_holds(db, armed_battle):
    """The one path through: eligible, owned as snapshotted, both reserved."""
    svc = BattleService(db)
    repo = BattleRepository(db)
    battle_id = str(armed_battle["id"])
    await _ack_both(svc, armed_battle)
    await db.commit()

    assert await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"])
    await db.commit()

    started = await repo.start_if_still_eligible(
        battle_id=battle_id, lease_token=str(uuid.uuid4()), lease_seconds=60
    )
    await db.commit()
    assert started is not None
    assert started["status"] == BattleStatus.RUNNING.value
    assert started["deadline_at"] is not None


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

    queued = await _try_queue(svc, db, battle_id, armed_battle["readiness_generation"])
    await db.commit()
    assert queued is not None
    assert queued["status"] == BattleStatus.QUEUED.value
    assert queued["queued_at"] is not None


# ── Public read routes (step 9): submissions and the verdict ──────────


@pytest_asyncio.fixture(loop_scope="module")
async def client(session_maker):
    """The real app with only the DB swapped. No auth override: these are public."""

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _running_battle(db, task_id: str, make_agent) -> str:
    """Drive a battle to 'running' through the real state machine."""
    repo = BattleRepository(db)
    svc = BattleService(db)
    agent_a, agent_b = await make_agent(), await make_agent()

    battle_id = await repo._create_battle(
        task_category=None,
        task_difficulty=None,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=str(uuid.uuid4()),
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=str(uuid.uuid4()),
    )
    await repo._mark_accepted(battle_id)
    await repo.reserve_both(battle_id, agent_a, agent_b, 600)
    ev_a = await svc.events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    ev_b = await svc.events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
    await repo._mark_queued(battle_id, row["readiness_generation"])
    await repo._mark_running(battle_id, str(uuid.uuid4()), 600)
    await db.commit()
    return battle_id


async def _drive_to_completed(db, battle_id: str) -> None:
    """running -> judging -> completed, with one full replicate pair recorded."""
    repo = BattleRepository(db)
    token = str(uuid.uuid4())
    await db.execute(
        text(
            "UPDATE battles SET lease_token = CAST(:t AS UUID), "
            "lease_expires_at = NOW() + INTERVAL '10 minutes', "
            "deadline_at = NOW() - INTERVAL '1 second', "
            "started_at = NOW() - INTERVAL '10 minutes', "
            "queued_at = NOW() - INTERVAL '20 minutes', "
            "challenged_at = NOW() - INTERVAL '30 minutes' WHERE id = CAST(:b AS UUID)"
        ),
        {"t": token, "b": battle_id},
    )
    assert await repo.mark_judging(battle_id, token) is not None

    # Both raw halves of one replicate, then its collapsed vote — the pairing
    # the verdict route must expose as evidence.
    for order in (PresentedOrder.AB, PresentedOrder.BA):
        run_id = await repo.create_judge_run(
            battle_id, JudgeKind.LLM.value, "zai/glm-4.5-flash", "seed0001", order.value
        )
        claimed = await repo.claim_judge_run(run_id, str(uuid.uuid4()), 60)
        await repo.complete_judge_run(
            run_id, str(claimed["lease_token"]), Vote.A.value, 0.8, "a is better",
            {"correctness": 0.9},
        )
    await repo.upsert_judgement(
        battle_id=battle_id,
        judge_kind=JudgeKind.LLM.value,
        judge_ref="zai/glm-4.5-flash",
        replicate_seed="seed0001",
        vote=Vote.A.value,
        confidence=0.8,
        reasoning="a is better",
        scores={"correctness": 0.9},
    )
    assert await repo.finalize(battle_id, token, "a", "1 for alpha-side", 1200, 1200, 1216, 1184)
    await db.commit()


async def test_submissions_route_is_empty_before_anyone_answers(client, db, task_id, make_agent):
    battle_id = await _running_battle(db, task_id, make_agent)
    response = await client.get(f"/api/v1/battles/{battle_id}/submissions")
    assert response.status_code == 200
    assert response.json() == []


async def test_public_sees_no_live_metadata_while_running(
    client, db, task_id, make_agent
):
    """The timing leak: an anonymous poller must not read either side's live rows.

    Per-turn metadata (seq_no, is_final, tokens_used, received_at) reveals that a
    fighter already went final and how much it spent — enough for a last-mover
    advantage. So while the battle is RUNNING the public / non-fighter caller
    sees an empty list, not the opponent's progress.
    """
    battle_id = await _running_battle(db, task_id, make_agent)
    await BattleRepository(db).add_submission(
        battle_id, Side.A, 1, "MY SECRET ANSWER", is_final=True, tokens_used=7
    )
    await db.commit()

    body = (await client.get(f"/api/v1/battles/{battle_id}/submissions")).json()

    assert body == []
    assert "MY SECRET ANSWER" not in str(body)


async def test_a_fighter_sees_only_its_own_side_while_running(
    client, db, task_id, make_agent
):
    """A fighter may watch its OWN turns live, never the opponent's.

    Mutation proof: drop the per-fighter filter and B's rows appear in A's view,
    handing A the exact finality/token signal the leak is about.
    """
    repo = BattleRepository(db)
    agent_a, agent_b = await make_agent(), await make_agent()
    battle_id = await repo._create_battle(
        task_category=None,
        task_difficulty=None, agent_a_id=agent_a, agent_a_owner_snapshot=str(uuid.uuid4()),
        challenge_ttl_seconds=3600, agent_b_id=agent_b, agent_b_owner_snapshot=str(uuid.uuid4()),
    )
    await repo._mark_accepted(battle_id)
    await repo.reserve_both(battle_id, agent_a, agent_b, 600)
    svc = BattleService(db)
    ev_a = await svc.events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    ev_b = await svc.events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
    await repo._mark_queued(battle_id, row["readiness_generation"])
    await repo._mark_running(battle_id, str(uuid.uuid4()), 600)
    await repo.add_submission(battle_id, Side.A, 1, "A's draft", is_final=False, tokens_used=3)
    await repo.add_submission(battle_id, Side.B, 1, "B is DONE", is_final=True, tokens_used=99)
    await db.commit()

    # Fighter A: sees only its own side, content still withheld.
    app.dependency_overrides[_optional_fighter] = lambda: {"id": agent_a}
    try:
        body = (await client.get(f"/api/v1/battles/{battle_id}/submissions")).json()
    finally:
        app.dependency_overrides.pop(_optional_fighter, None)

    assert {row["side"] for row in body} == {"a"}
    assert all(row["content"] is None and row["content_withheld"] for row in body)
    # B's finality and token count never reach A.
    assert "B is DONE" not in str(body)
    assert 99 not in [row["tokens_used"] for row in body]

    # Fighter B: symmetric — only its own side.
    app.dependency_overrides[_optional_fighter] = lambda: {"id": agent_b}
    try:
        body_b = (await client.get(f"/api/v1/battles/{battle_id}/submissions")).json()
    finally:
        app.dependency_overrides.pop(_optional_fighter, None)
    assert {row["side"] for row in body_b} == {"b"}


async def test_submissions_reveal_content_once_the_battle_is_completed(
    client, db, task_id, make_agent
):
    battle_id = await _running_battle(db, task_id, make_agent)
    repo = BattleRepository(db)
    await repo.add_submission(battle_id, Side.A, 1, "A's answer", is_final=True)
    await repo.add_submission(
        battle_id, Side.B, 1, None, is_final=True, truncated=True,
        error="no submission before deadline",
    )
    await db.commit()
    await _drive_to_completed(db, battle_id)

    body = (await client.get(f"/api/v1/battles/{battle_id}/submissions")).json()

    by_side = {row["side"]: row for row in body}
    assert by_side["a"]["content"] == "A's answer"
    assert by_side["a"]["content_withheld"] is False
    # The silent fighter reads as timed-out, and its error is a TYPE not a value.
    assert by_side["b"]["content"] is None
    assert by_side["b"]["truncated"] is True
    assert by_side["b"]["error"] == "no submission before deadline"


async def test_the_verdict_is_withheld_until_the_battle_completes(
    client, db, task_id, make_agent
):
    """THE gate. A fighter must never watch itself being scored mid-battle."""
    battle_id = await _running_battle(db, task_id, make_agent)
    repo = BattleRepository(db)
    # A real, complete judgement exists — the gate is the ONLY thing hiding it.
    await repo.upsert_judgement(
        battle_id=battle_id,
        judge_kind=JudgeKind.LLM.value,
        judge_ref="zai/glm-4.5-flash",
        replicate_seed="seed0001",
        vote=Vote.A.value,
        confidence=0.9,
        reasoning="LEAKED REASONING",
        scores={"correctness": 1.0},
    )
    run_id = await repo.create_judge_run(
        battle_id, JudgeKind.LLM.value, "zai/glm-4.5-flash", "seed0001", PresentedOrder.AB.value
    )
    claimed = await repo.claim_judge_run(run_id, str(uuid.uuid4()), 60)
    await repo.complete_judge_run(
        run_id, str(claimed["lease_token"]), Vote.A.value, 0.9, "LEAKED REASONING"
    )
    await db.commit()

    response = await client.get(f"/api/v1/battles/{battle_id}/judgements")

    assert response.status_code == 200
    body = response.json()
    assert body == {"judgements": [], "runs": [], "tallies": {}}
    # The strongest form: no fragment of the verdict escapes anywhere.
    assert "LEAKED REASONING" not in response.text
    assert "seed0001" not in response.text


async def test_a_completed_battle_publishes_its_votes_and_the_raw_pair(
    client, db, task_id, make_agent
):
    battle_id = await _running_battle(db, task_id, make_agent)
    await _drive_to_completed(db, battle_id)

    body = (await client.get(f"/api/v1/battles/{battle_id}/judgements")).json()

    assert len(body["judgements"]) == 1
    assert body["judgements"][0]["vote"] == "a"
    assert body["judgements"][0]["scores"] == {"correctness": 0.9}
    assert body["judgements"][0]["position_sensitive"] is False

    # The evidence: two raw runs, one per presentation order, same seed. This is
    # what lets a spectator CHECK the position-bias control.
    assert len(body["runs"]) == 2
    assert {run["presented_order"] for run in body["runs"]} == {"ab", "ba"}
    assert {run["replicate_seed"] for run in body["runs"]} == {"seed0001"}

    # Quorum arithmetic, split per kind and shown rather than asserted.
    assert body["tallies"] == {
        "llm": {
            "votes_for_a": 1, "votes_for_b": 0, "ties": 0, "abstained": 0,
            "errored": 0, "valid": 1, "position_sensitive": 0,
        }
    }


async def test_abstentions_and_errors_stay_out_of_the_quorum_denominator(
    client, db, task_id, make_agent
):
    battle_id = await _running_battle(db, task_id, make_agent)
    await _drive_to_completed(db, battle_id)
    repo = BattleRepository(db)
    for seed, vote in (("seed0002", Vote.ABSTAIN), ("seed0003", Vote.ERROR)):
        await repo.upsert_judgement(
            battle_id=battle_id,
            judge_kind=JudgeKind.LLM.value,
            judge_ref="zai/glm-4.5-flash",
            replicate_seed=seed,
            vote=vote.value,
        )
    await db.commit()

    tally = (await client.get(f"/api/v1/battles/{battle_id}/judgements")).json()["tallies"]["llm"]

    # Three collapsed votes, but only ONE counts — a panel of errors is not
    # unanimous, and `valid` is the number the quorum is measured against.
    assert tally["valid"] == 1
    assert tally["abstained"] == 1
    assert tally["errored"] == 1


async def test_read_routes_404_on_an_unknown_battle(client):
    missing = uuid.uuid4()
    assert (await client.get(f"/api/v1/battles/{missing}/submissions")).status_code == 404
    assert (await client.get(f"/api/v1/battles/{missing}/judgements")).status_code == 404


# ── D: the public battle DTO must not leak the ownership graph ─────────────


async def _new_user(db) -> str:
    uid = str(uuid.uuid4())
    await db.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"u-{uid[:8]}@example.test"},
    )
    await db.commit()
    return uid


async def _pending_challenge(db, owner_id, task_id, make_agent) -> tuple[str, str, str]:
    """A named, still-pending challenge whose agent B has a DIFFERENT owner.

    Returns (battle_id, challenger_owner, target_owner). The distinct owner is
    the point: it lets the accept-capability test prove the challenger's owner
    (agent A) canNOT see the accept button, only agent B's owner can.
    """
    target_owner = await _new_user(db)
    challenger = await make_agent()  # owned by owner_id
    target = await make_agent(owner=target_owner)
    battle_id = await BattleService(db).create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=target,
    )
    await db.commit()
    return battle_id, owner_id, target_owner


async def test_public_battle_detail_never_ships_owner_uuids(
    client, db, owner_id, task_id, make_agent
):
    """Anyone reading a battle must not be able to enumerate shared owners."""
    battle_id, challenger_owner, target_owner = await _pending_challenge(
        db, owner_id, task_id, make_agent
    )

    resp = await client.get(f"/api/v1/battles/{battle_id}")
    assert resp.status_code == 200
    body = resp.json()

    # The two owner-snapshot fields are gone entirely — not null, absent.
    assert "agent_a_owner_snapshot" not in body
    assert "agent_b_owner_snapshot" not in body
    # Neither owner id appears anywhere in the payload.
    assert challenger_owner not in resp.text
    assert target_owner not in resp.text
    # An anonymous reader has no accept capability.
    assert body["viewer_can_accept"] is False


async def test_viewer_can_accept_is_true_only_for_the_opponent_owner(
    client, db, owner_id, task_id, make_agent
):
    """Capability matches the accept CAS: only agent B's current owner sees True."""
    battle_id, challenger_owner, target_owner = await _pending_challenge(
        db, owner_id, task_id, make_agent
    )

    async def _flag_for(user_id: str) -> bool:
        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=user_id)
        try:
            return (await client.get(f"/api/v1/battles/{battle_id}")).json()["viewer_can_accept"]
        finally:
            app.dependency_overrides.pop(get_optional_user, None)

    # Only agent B's owner may accept.
    assert await _flag_for(target_owner) is True
    # The challenger's owner (agent A) may NOT — proves it is not just "am I a
    # party to this battle". A frozen-snapshot check that ignored which side
    # would still (wrongly) let agent A's owner through here.
    assert await _flag_for(challenger_owner) is False
    # A stranger with a valid session may not.
    assert await _flag_for(str(uuid.uuid4())) is False


# ── B: a directly-challenged owner is notified at creation, not by browsing ──


async def test_named_challenge_notifies_the_target_owner(
    client, db, owner_id, task_id, make_agent
):
    """The first touch: creating a named challenge notifies the target's owner."""
    challenger = await make_agent()
    target = await make_agent()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    notify = AsyncMock()
    try:
        with patch("app.api.v1.battles._notify_battle_owners", notify):
            resp = await client.post(
                "/api/v1/battles",
                json={"task_id": task_id, "agent_a_id": challenger, "agent_b_id": target},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 201
    notify.assert_awaited_once()
    recipients = notify.await_args.args[2]
    assert len(recipients) == 1
    assert recipients[0][0] == target
    assert recipients[0][1] == "battle_challenge_received"


async def test_claiming_an_open_challenge_notifies_the_challenger(
    client, db, owner_id, task_id, make_agent
):
    """Claiming an OPEN challenge notifies the waiting challenger, not the claimer."""
    challenger = await make_agent()
    battle_id = await BattleService(db).create_challenge(
        task_category=None,
        task_difficulty=None,
        agent_a_id=challenger,
        challenger_owner_user_id=owner_id,
        agent_b_id=None,
    )
    await db.commit()

    claimant = await make_agent()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    notify = AsyncMock()
    try:
        with patch("app.api.v1.battles._notify_battle_owners", notify):
            resp = await client.post(
                f"/api/v1/battles/{battle_id}/claim", json={"agent_id": claimant}
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    notify.assert_awaited_once()
    recipients = notify.await_args.args[2]
    assert recipients[0][0] == challenger  # the challenger, not the claimant
    assert recipients[0][1] == "battle_challenge_received"


async def test_a_notify_failure_does_not_roll_back_the_challenge(
    client, db, owner_id, task_id, make_agent
):
    """Best-effort: the challenge is durable even when the notification blows up.

    No mock here — the test schema has no notifications table, so the REAL
    create_notification_task raises, and _notify_battle_owners must swallow it.
    The battle row must still be committed.
    """
    challenger = await make_agent()
    target = await make_agent()

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    try:
        resp = await client.post(
            "/api/v1/battles",
            json={"task_id": task_id, "agent_a_id": challenger, "agent_b_id": target},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 201
    # The challenge survived the notification failure.
    assert await _count_battles(db, target) == 1
