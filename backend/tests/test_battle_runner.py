"""Tests for backend/app/services/battle_runner.py — step 9's settlement.

THE invariant under test, stated so it can be falsified:

    Settlement is idempotent and Elo is applied EXACTLY ONCE. Two workers
    settling the same battle concurrently change the ratings once, not twice
    and not zero times.

This is an integration suite by necessity, not by preference. The property is
arbitrated by a real compare-and-set inside a real transaction against real row
locks; a mock would only prove that a mock returns what it was told to. So these
run the REAL V66 migration against testcontainers Postgres — a missing CHECK or
a typo in the migration must fail here, which is the point of testing it.

The judge model is mocked. What is under test is the settlement transaction, not
the panel: feeding it real LLM calls would make it slow, flaky, and no more
convincing about the thing being asserted.

NOT covered here, stated plainly: the full reconcile_once() loop driven by the
live BattleRunTask, and judge-panel HTTP behaviour. The task IS registered in
background.py ALL_TASKS, but nothing here exercises the scheduler itself — the
wiring is proven only by an import smoke check, not by a test.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.rating import DEFAULT_ELO, ELO_FLOOR, K_FACTOR
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository, ReservationConflictError
from app.schemas.battles import BattleStatus, Side, TaskSource, Vote, Winner
from app.services.battle_judges import (
    JUDGE_KIND_LLM,
    JUDGE_MODEL,
    JudgeRunResult,
    JudgeTransportError,
    replicate_seed,
)
from app.services.battle_runner import (
    BATTLE_LEASE_SECONDS,
    JUDGE_RUN_MAX_ATTEMPTS,
    POLL_MAX_ATTEMPTS,
    RECONCILE_BATCH,
    RUNNING_MAX_ATTEMPTS,
    SILENT_FIGHTER_SEQ_NO,
    BattleRunner,
    _battle_result_title,
    _notify_battle_owners,
    reap_once,
    reconcile_once,
)
from app.services.battle_service import READY_MAX_GENERATIONS, BattleService
from app.services.connection_manager import DeliveryResult

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"

RUBRIC = [{"key": "correctness", "description": "Does it work?", "weight": 1.0}]

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
    -- The chain test drives real admission, and admission reads these: an
    -- agent must be active, not hosted, owned, and opted in. V66 adds
    -- available_for_battles by ALTER, so only the pre-existing ones are here.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_hosted BOOLEAN NOT NULL DEFAULT FALSE,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
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
async def db_session(session_maker):
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def task_id(db_session) -> str:
    uid = str(uuid.uuid4())
    await db_session.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"o-{uid[:8]}@example.test"},
    )
    repo = BattleRepository(db_session)
    tid = await repo.create_task(
        source=TaskSource.GENERATED,
        title="Write a parser",
        prompt="Parse this log format.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        created_by_user_id=uid,
    )
    await db_session.commit()
    return tid


async def _new_owner(session) -> str:
    uid = str(uuid.uuid4())
    await session.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"o-{uid[:8]}@example.test"},
    )
    return uid


async def _new_agent(session, elo: int = DEFAULT_ELO) -> str:
    aid = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO agents (id, handle, name, battle_elo) "
            "VALUES (CAST(:id AS UUID), :h, 'F', :elo)"
        ),
        {"id": aid, "h": f"f-{aid[:8]}", "elo": elo},
    )
    return aid


async def _battle_in_judging(
    session,
    task_id: str,
    *,
    votes: list[Vote],
    elo_a: int = DEFAULT_ELO,
    elo_b: int = DEFAULT_ELO,
    same_owner: bool = False,
    agent_a: str | None = None,
    agent_b: str | None = None,
) -> tuple[str, str, str, str]:
    """Drive a battle to 'judging' with collapsed votes. Returns ids + token.

    Built through the real state machine rather than by INSERTing a 'judging'
    row: a battle assembled by hand could satisfy the settlement CAS while being
    a state the machine can never actually produce, and the test would prove
    nothing about the real path.

    ``agent_a``/``agent_b`` may name EXISTING fighters, which is what lets a
    caller build two battles sharing one — see the lost-update test.
    """
    repo = BattleRepository(session)
    events = AgentEventRepository(session)

    owner_a = await _new_owner(session)
    owner_b = owner_a if same_owner else await _new_owner(session)
    agent_a = agent_a or await _new_agent(session, elo_a)
    agent_b = agent_b or await _new_agent(session, elo_b)

    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_a,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_b,
    )
    assert await repo._mark_accepted(battle_id) is not None
    assert len(await repo.reserve_both(battle_id, agent_a, agent_b, 600)) == 2

    event_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    event_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, event_a, event_b, 60)
    assert row is not None
    assert await repo._mark_queued(battle_id, row["readiness_generation"]) is not None

    token = str(uuid.uuid4())
    assert await repo._mark_running(battle_id, token, 600) is not None

    # Both sides answer, so mark_judging's precondition holds without waiting
    # out a real 600s wall clock.
    for side in (Side.A, Side.B):
        assert await repo.add_submission(battle_id, side, 1, "an answer", is_final=True)
    assert await repo.mark_judging(battle_id, token) is not None

    for i, vote in enumerate(votes):
        await repo.upsert_judgement(
            battle_id=battle_id,
            judge_kind=JUDGE_KIND_LLM,
            judge_ref=JUDGE_MODEL,
            replicate_seed=replicate_seed(battle_id, i),
            vote=vote.value,
            confidence=0.8,
        )
    await session.commit()
    return battle_id, agent_a, agent_b, token


async def _elo(session_maker, agent_id: str) -> int:
    async with session_maker() as session:
        row = await session.execute(
            text("SELECT battle_elo FROM agents WHERE id = CAST(:id AS UUID)"), {"id": agent_id}
        )
        return int(row.scalar_one())


async def _counters(session_maker, agent_id: str) -> tuple[int, int, int]:
    async with session_maker() as session:
        row = await session.execute(
            text(
                "SELECT battle_wins, battle_losses, battle_ties "
                "FROM agents WHERE id = CAST(:id AS UUID)"
            ),
            {"id": agent_id},
        )
        return tuple(row.first())


async def _settle_in_own_session(session_maker, battle_id: str, token: str):
    """Settle in an independent transaction — its own connection, its own race.

    Both callers get the SAME lease token on purpose: that is the real
    post-restart shape (a former leader and its replacement both hold the token
    they claimed with), and it strips away the token check so that ONLY the
    status/finalized_at CAS can arbitrate. If settlement is not idempotent, this
    is where it shows.
    """
    async with session_maker() as session:
        runner = BattleRunner(session, gate=None)
        return await runner.settle_battle(battle_id, token)


class TestSettlementIsExactlyOnce:
    """THE invariant."""

    async def test_two_concurrent_settlements_apply_elo_exactly_once(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.B]
        )

        before_a = await _elo(session_maker, agent_a)
        before_b = await _elo(session_maker, agent_b)
        assert (before_a, before_b) == (DEFAULT_ELO, DEFAULT_ELO)

        # The race, on two real connections.
        first, second = await asyncio.gather(
            _settle_in_own_session(session_maker, battle_id, token),
            _settle_in_own_session(session_maker, battle_id, token),
            return_exceptions=True,
        )

        for outcome in (first, second):
            assert not isinstance(outcome, Exception), outcome

        # Exactly one worker settled; the other found the battle already done.
        applied = [c for c in (first, second) if c is not None]
        assert len(applied) == 1, f"expected one winner, got {len(applied)}"

        after_a = await _elo(session_maker, agent_a)
        after_b = await _elo(session_maker, agent_b)

        # A won 2-1 from an even rating: +16 / -16, applied ONCE.
        assert after_a == DEFAULT_ELO + K_FACTOR // 2
        assert after_b == DEFAULT_ELO - K_FACTOR // 2
        assert after_a - before_a == -(after_b - before_b)

        # Counters moved once too — a rating without a recorded battle, or a
        # battle counted twice, is exactly what a second settlement would give.
        assert await _counters(session_maker, agent_a) == (1, 0, 0)
        assert await _counters(session_maker, agent_b) == (0, 1, 0)

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"
        assert battle["winner"] == "a"
        assert battle["finalized_at"] is not None
        assert (battle["elo_a_before"], battle["elo_a_after"]) == (DEFAULT_ELO, after_a)

        # And the reservations are gone, so both fighters can battle again.
        async with session_maker() as session:
            held = await session.execute(
                text("SELECT COUNT(*) FROM battle_reservations WHERE battle_id = CAST(:b AS UUID)"),
                {"b": battle_id},
            )
            assert held.scalar_one() == 0

    async def test_settling_an_already_completed_battle_is_a_no_op(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, _, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        assert await _settle_in_own_session(session_maker, battle_id, token) is not None
        settled_elo = await _elo(session_maker, agent_a)

        # A late worker retrying after a restart must not re-apply the verdict.
        assert await _settle_in_own_session(session_maker, battle_id, token) is None
        assert await _elo(session_maker, agent_a) == settled_elo

    async def test_a_worker_with_a_stale_token_cannot_settle(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, _, _token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        # Its verdict is honest, but it lost the row: a newer owner is
        # authoritative, so this result must be discarded.
        assert await _settle_in_own_session(session_maker, battle_id, str(uuid.uuid4())) is None
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO


class TestNearFloorSettlementDoesNotStrand:
    """A heavy loss from a near-floor rating must SETTLE, not violate the CHECK.

    The bug: rating.new_rating had no floor, so a low-rated agent losing to a
    comparably-low one computes a rating that rounds to <= 0. V66's
    battle_elo_positive CHECK requires elo>0, so finalize's UPDATE raises a
    CheckViolation, the battle can never reach 'completed', and it strands in
    'judging' until the attempt cap aborts it — the true result lost. The floor
    keeps every computed rating clear of the CHECK, so settlement succeeds.
    """

    async def test_a_near_floor_loser_settles_above_the_floor(
        self, session_maker, db_session, task_id
    ) -> None:
        # Both agents at elo 10: equal, so E=0.5, and the loser's raw new rating
        # is round(10 + 32*(0 - 0.5)) = -6 — a direct CHECK violation without the
        # clamp. Mutation proof: drop the floor in new_rating and this raises a
        # CheckViolation inside settle_battle, so the test errors.
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.B, Vote.B, Vote.B], elo_a=10, elo_b=10
        )

        change = await _settle_in_own_session(session_maker, battle_id, token)
        assert change is not None, "settlement must succeed, not raise a CheckViolation"

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"
        assert battle["winner"] == "b"
        # The loser's persisted rating sits at the floor, never at or below zero.
        assert battle["elo_a_after"] >= ELO_FLOOR
        assert battle["elo_a_after"] == ELO_FLOOR
        assert await _elo(session_maker, agent_a) >= ELO_FLOOR


async def _lapse_ready_lease(db_session, battle_id: str) -> None:
    """Force a reserved battle's ready lease into the past. Commits."""
    await db_session.execute(
        text(
            "UPDATE battles SET ready_lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE id = CAST(:b AS UUID)"
        ),
        {"b": battle_id},
    )
    await db_session.commit()


async def _ack_both_before_lease(db_session, battle_id: str) -> None:
    """ACK both current ready events, then backdate to BEFORE the lapsed lease.

    Reproduces the finding-1 shape with a timeline that satisfies every V65
    CHECK: dispatched(-20s) <= acked(-15s) < lease(-10s) < NOW() < expires. The
    ACK lands in time (acked_at < ready_lease_expires_at) while the lease has
    since expired and the reconciler polls late. Uses the real mark_acked
    statement first (so status/dispatched_at/acked_at are set legally), then
    shifts the timestamps into that ordering.
    """
    repo = BattleRepository(db_session)
    svc = BattleService(db_session)
    battle = await repo.get(battle_id)
    await svc.events.mark_acked(
        str(battle["agent_a_id"]), [str(battle["ready_check_event_id_a"])]
    )
    await svc.events.mark_acked(
        str(battle["agent_b_id"]), [str(battle["ready_check_event_id_b"])]
    )
    await db_session.commit()
    await db_session.execute(
        text(
            """
            UPDATE agent_events e
            SET dispatched_at = NOW() - INTERVAL '20 seconds',
                acked_at = NOW() - INTERVAL '15 seconds'
            FROM battles b
            WHERE b.id = CAST(:bid AS UUID)
              AND e.event_id IN (b.ready_check_event_id_a, b.ready_check_event_id_b)
            """
        ),
        {"bid": battle_id},
    )
    await db_session.execute(
        text(
            "UPDATE battles SET ready_lease_expires_at = NOW() - INTERVAL '10 seconds' "
            "WHERE id = CAST(:bid AS UUID)"
        ),
        {"bid": battle_id},
    )
    await db_session.commit()


class TestReadinessGriefIsBounded:
    """A never-ACK opponent cannot pin the challenger for the whole 24h TTL.

    The bug: a missed readiness lease returns a battle to 'accepted' and the next
    reconcile re-arms it, so accept-then-never-ACK re-reserves the challenger
    every ~lease for the full challenge TTL with no Elo consequence. The fix
    bounds the re-arm attempts: after READY_MAX_GENERATIONS the battle is aborted
    (naming the silent side) and both reservations are released — but a fighter
    that ACKed in time is NEVER aborted, even if the reconciler polls late.
    """

    async def _accepted_battle(self, db_session, task_id) -> tuple[str, str, str]:
        repo = BattleRepository(db_session)
        owner_a = await _new_owner(db_session)
        owner_b = await _new_owner(db_session)
        agent_a = await _new_eligible_agent(db_session, owner_a)
        agent_b = await _new_eligible_agent(db_session, owner_b)
        battle_id = await repo._create_battle(
            task_id=task_id, agent_a_id=agent_a, agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600, agent_b_id=agent_b, agent_b_owner_snapshot=owner_b,
        )
        assert await repo._mark_accepted(battle_id) is not None
        await db_session.commit()
        return battle_id, agent_a, agent_b

    async def _rearm_to_cap_unacked(self, session_maker, db_session, task_id):
        """Drive REAL accepted<->reserved re-arms to the cap, never ACKing.

        Proves the generation counter increments per real re-arm and the
        reserved->accepted->reserved lifecycle survives — not a hand-set
        generation. Ends 'reserved' at generation == cap, lease lapsed, no ACKs.
        """
        svc = BattleService(db_session)
        battle_id, agent_a, agent_b = await self._accepted_battle(db_session, task_id)
        for expected_gen in range(1, READY_MAX_GENERATIONS + 1):
            armed = await svc.arm_readiness(battle_id)
            assert armed is not None
            assert armed["readiness_generation"] == expected_gen  # per real re-arm
            await db_session.commit()
            await _lapse_ready_lease(db_session, battle_id)
            if expected_gen < READY_MAX_GENERATIONS:
                outcome = await svc.expire_or_abort_readiness(battle_id)
                await db_session.commit()
                assert outcome is not None and outcome["outcome"] == "released"
                async with session_maker() as s:
                    assert (await BattleRepository(s).get(battle_id))["status"] == "accepted"
        return battle_id, agent_a, agent_b

    async def test_rearm_lifecycle_increments_then_aborts_at_the_cap(
        self, session_maker, db_session, task_id
    ) -> None:
        """Real re-arms to the cap, then abort: freed fighters + owner notice.

        Mutation proof: swap expire_or_abort_readiness back to the old
        release-only path and the battle returns to 'accepted' not 'aborted', the
        reservations survive, and no notification fires.
        """
        battle_id, agent_a, agent_b = await self._rearm_to_cap_unacked(
            session_maker, db_session, task_id
        )
        assert await _reservation_count(session_maker, battle_id) == 2

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            battle = await runner.repo.get(battle_id)
            with patch(
                "app.services.battle_runner._notify_battle_owners", new=AsyncMock()
            ) as notify:
                result = await runner.admit_reserved(battle)
            assert result is False  # did not queue — took the terminal exit
            recipients = notify.await_args.args[2]
            assert {r[0] for r in recipients} == {agent_a, agent_b}
            assert all(r[1] == "battle_aborted" for r in recipients)

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "aborted"
        assert "did not ACK" in battle["verdict_reason"]
        assert "both fighters" in battle["verdict_reason"]
        assert await _reservation_count(session_maker, battle_id) == 0

    async def test_two_misses_then_a_third_ack_queues(
        self, session_maker, db_session, task_id
    ) -> None:
        """The legitimate path: miss two windows, ACK the third -> queued.

        The bound must not punish an agent that eventually shows up while the
        challenge is live. Drives the full re-arm lifecycle, then ACKs on gen 3.
        """
        svc = BattleService(db_session)
        battle_id, agent_a, agent_b = await self._accepted_battle(db_session, task_id)

        for expected_gen in (1, 2):
            armed = await svc.arm_readiness(battle_id)
            assert armed["readiness_generation"] == expected_gen
            await db_session.commit()
            await _lapse_ready_lease(db_session, battle_id)
            outcome = await svc.expire_or_abort_readiness(battle_id)
            await db_session.commit()
            assert outcome["outcome"] == "released"

        armed = await svc.arm_readiness(battle_id)
        assert armed["readiness_generation"] == READY_MAX_GENERATIONS
        await db_session.commit()
        for side, agent in (("a", agent_a), ("b", agent_b)):
            await svc.events.mark_acked(agent, [str(armed[f"ready_check_event_id_{side}"])])
        await db_session.commit()

        queued = await svc.try_queue(battle_id, armed["readiness_generation"])
        await db_session.commit()
        assert queued is not None
        assert queued["status"] == "queued"

        # Cleanup: terminal, so the whole-chain test's global counts stay clean.
        repo = BattleRepository(db_session)
        await repo.mark_aborted(battle_id, "test cleanup")
        await repo.release_reservations(battle_id)
        await db_session.commit()

    async def test_a_valid_ack_at_the_lease_boundary_is_not_aborted(
        self, session_maker, db_session, task_id
    ) -> None:
        """Finding 1: an ACK that landed BEFORE the lease expired must not abort.

        Both fighters ACK validly (acked_at < ready_lease_expires_at), the lease
        then lapses, and the reconciler polls a moment later while the battle is
        AT the cap. The naive bound would abort a fighter that ACKed in time.

        Two mutation directions, both covered:
        * abort guard (repo.abort_unready_readiness NOT-acked clause): without it
          expire_or_abort_readiness aborts despite valid ACKs -> assertion 1 flips.
        * admission grace (admit_to_queue dropping ready_lease_expires_at > NOW()):
          without it admit_reserved cannot queue a lapsed-lease battle even with
          valid ACKs -> assertion 2 flips.
        """
        battle_id, agent_a, agent_b = await self._rearm_to_cap_unacked(
            session_maker, db_session, task_id
        )
        await _ack_both_before_lease(db_session, battle_id)

        # Assertion 1 — the abort guard: a validly-ACKed battle is NOT aborted.
        outcome = await BattleService(db_session).expire_or_abort_readiness(battle_id)
        await db_session.commit()
        assert outcome is None, f"a validly-ACKed battle was aborted: {outcome}"
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "reserved"

        # Assertion 2 — the admission grace: admit_reserved QUEUES it.
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            battle = await runner.repo.get(battle_id)
            assert await runner.admit_reserved(battle) is True
        async with session_maker() as session:
            queued = await BattleRepository(session).get(battle_id)
        assert queued["status"] == "queued"

        # Cleanup: terminal, so the whole-chain test's global counts stay clean.
        repo = BattleRepository(db_session)
        await repo.mark_aborted(battle_id, "test cleanup")
        await repo.release_reservations(battle_id)
        await db_session.commit()

    async def test_a_concurrent_committing_ack_blocks_and_defeats_the_abort(
        self, session_maker, db_session, task_id
    ) -> None:
        """Real two-connection race: FOR UPDATE serializes ACK-vs-abort.

        What this PROVES: session A holds the two ready-check event rows locked
        with a VALID (pre-lease) ACK not yet committed; session B's abort BLOCKS
        on those rows (rather than reading them unacked under READ COMMITTED);
        once A commits the ACK, B's abort re-evaluates, sees it, and SKIPS. So an
        ACK committing before the abort finishes can never abort the fighter.

        What it does NOT prove: the reverse lock order (abort acquiring the rows
        first legitimately wins — the fighter's ACK was genuinely not yet
        recorded). That is not a defect: it is the abort winning a real race, not
        a lost read. This test pins only the lost-read direction the FOR UPDATE
        closes; mutation-revert of the FOR UPDATE makes B read the rows unacked
        without blocking and abort despite the valid ACK.
        """
        battle_id, agent_a, agent_b = await self._rearm_to_cap_unacked(
            session_maker, db_session, task_id
        )
        battle = await BattleRepository(db_session).get(battle_id)
        ev_a = str(battle["ready_check_event_id_a"])
        ev_b = str(battle["ready_check_event_id_b"])

        async with session_maker() as s_ack, session_maker() as s_abort:
            # session A: a VALID ACK (dispatched<=acked<lease), applied but NOT
            # committed — so it holds the event rows' write locks.
            await s_ack.execute(
                text(
                    """
                    UPDATE agent_events
                    SET status = 'acked',
                        dispatched_at = NOW() - INTERVAL '20 seconds',
                        acked_at = NOW() - INTERVAL '15 seconds'
                    WHERE event_id IN (CAST(:a AS UUID), CAST(:b AS UUID))
                    """
                ),
                {"a": ev_a, "b": ev_b},
            )

            # session B: the abort must BLOCK on the FOR UPDATE of those rows.
            contender = asyncio.create_task(
                BattleService(s_abort).expire_or_abort_readiness(battle_id)
            )
            await asyncio.sleep(0.4)
            assert not contender.done(), "abort did not block on the locked ready-event rows"

            await s_ack.commit()  # the valid ACK lands
            outcome = await asyncio.wait_for(contender, timeout=5.0)
            await s_abort.commit()
            assert outcome is None, "abort fired despite an ACK that committed first"

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "reserved", "a validly-ACKed battle was aborted"

        # Cleanup: terminal, so the whole-chain test's global counts stay clean.
        repo = BattleRepository(db_session)
        await repo.mark_aborted(battle_id, "test cleanup")
        await repo.release_reservations(battle_id)
        await db_session.commit()


class TestSharedFighterLostUpdate:
    """The FOR UPDATE in lock_fighter_ratings — the guard, with its proof.

    This is a DIFFERENT race from the one above, and the CAS cannot cover it.
    ``finalize`` serialises the finalizers of ONE battle; this serialises the
    writers of one AGENT across two battles. Nothing in the battle row is
    contended here — two distinct rows settle — so only the agent row lock stops
    a read-modify-write from losing an update.

    Reachability, since ``battle_reservations.agent_id`` is a PRIMARY KEY and so
    normally pins a fighter to one battle: ``reserved_until`` is wall-clock and
    nothing renews it when a battle starts, so ``delete_expired_reservations()``
    can free a fighter WHILE their battle is still running. A second battle may
    then reserve them, and both can reach 'judging'. The path is narrow, but it
    is real, and it is exactly the state constructed below.
    """

    async def test_lock_fighter_ratings_blocks_a_second_writer_of_the_same_agent(
        self, session_maker, db_session, task_id
    ) -> None:
        """The lock itself, proven by the only thing that distinguishes it: BLOCKING.

        The settlement-level test below does NOT prove this, and it took a
        surviving mutant to notice. Two coroutines under asyncio.gather do not
        reliably interleave inside the read-modify-write window: whichever
        transaction starts first usually finishes before the other's SELECT
        runs, so the second reads the ALREADY-updated rating and produces the
        correct answer with or without the lock. Removing FOR UPDATE left that
        test green.

        So the property is forced here instead of hoped for: hold the lock in
        one transaction and assert the second writer CANNOT proceed until the
        first commits. That is exactly what FOR UPDATE buys and the only thing
        that dies when it is removed.
        """
        shared = await _new_agent(db_session)
        opponent_one = await _new_agent(db_session)
        opponent_two = await _new_agent(db_session)
        await db_session.commit()

        battle_one, _, _, _ = await _battle_in_judging(
            db_session,
            task_id,
            votes=[Vote.A, Vote.A, Vote.A],
            agent_a=shared,
            agent_b=opponent_one,
        )
        await db_session.execute(
            text("DELETE FROM battle_reservations WHERE agent_id = CAST(:a AS UUID)"), {"a": shared}
        )
        await db_session.commit()
        battle_two, _, _, _ = await _battle_in_judging(
            db_session,
            task_id,
            votes=[Vote.A, Vote.A, Vote.A],
            agent_a=shared,
            agent_b=opponent_two,
        )

        async with session_maker() as s1, session_maker() as s2:
            repo_one, repo_two = BattleRepository(s1), BattleRepository(s2)

            first = await repo_one.lock_fighter_ratings(battle_one)
            assert first["elo_a"] == DEFAULT_ELO

            # A second writer of the SAME agent, in a real concurrent transaction.
            contender = asyncio.create_task(repo_two.lock_fighter_ratings(battle_two))
            await asyncio.sleep(0.4)

            # THE assertion. With FOR UPDATE this is still waiting on s1's lock;
            # without it, it has already read a stale 1200 and a lost update is
            # inevitable.
            assert not contender.done(), "FOR UPDATE did not block: a lost update is possible"

            await repo_one.apply_rating(shared, DEFAULT_ELO + K_FACTOR // 2, "win")
            await s1.commit()

            second = await asyncio.wait_for(contender, timeout=5.0)
            # Having waited, it reads the COMMITTED rating — never the stale one.
            assert second["elo_a"] == DEFAULT_ELO + K_FACTOR // 2
            await s2.rollback()

    async def test_two_battles_sharing_a_fighter_both_rate_and_compose_sequentially(
        self, session_maker, db_session, task_id
    ) -> None:
        shared = await _new_agent(db_session)
        opponent_one = await _new_agent(db_session)
        opponent_two = await _new_agent(db_session)
        await db_session.commit()

        battle_one, _, _, token_one = await _battle_in_judging(
            db_session,
            task_id,
            votes=[Vote.A, Vote.A, Vote.A],
            agent_a=shared,
            agent_b=opponent_one,
        )

        # The reaper frees the shared fighter mid-battle: their reservation
        # lapsed on wall-clock time while battle_one was still running. This is
        # delete_expired_reservations()' effect, not a shortcut around the rule.
        await db_session.execute(
            text("DELETE FROM battle_reservations WHERE agent_id = CAST(:a AS UUID)"),
            {"a": shared},
        )
        await db_session.commit()

        battle_two, _, _, token_two = await _battle_in_judging(
            db_session,
            task_id,
            votes=[Vote.A, Vote.A, Vote.A],
            agent_a=shared,
            agent_b=opponent_two,
        )

        assert await _elo(session_maker, shared) == DEFAULT_ELO

        # Both battles settle at the same instant, on two real connections.
        first, second = await asyncio.gather(
            _settle_in_own_session(session_maker, battle_one, token_one),
            _settle_in_own_session(session_maker, battle_two, token_two),
            return_exceptions=True,
        )
        for outcome in (first, second):
            assert not isinstance(outcome, Exception), outcome

        # Both are real, distinct battles: BOTH must rate. (Unlike the
        # same-battle race, where exactly one wins.)
        assert first is not None and first.applied is True
        assert second is not None and second.applied is True

        # Two wins from 1200 against two 1200 opponents, applied SEQUENTIALLY:
        # 1200 -> 1216 (+16 at even odds), then 1216 vs 1200 -> +15 = 1231.
        # Order-independent, because both opponents are identical.
        # A lost update reads 1200 twice and writes 1216 twice -> 1216.
        assert await _elo(session_maker, shared) == 1231, "lost update: one rating write vanished"
        assert await _counters(session_maker, shared) == (2, 0, 0)

        # The opponents are ORDER-DEPENDENT, unlike the shared fighter. Whoever
        # settles first loses to a 1200-rated shared fighter (-16 -> 1184); the
        # other loses to a 1216-rated one, which is less costly (-15 -> 1185).
        # Which is which is a real race, so assert the SET — pinning either to a
        # single value would be a flaky test asserting a coin flip.
        assert {
            await _elo(session_maker, opponent_one),
            await _elo(session_maker, opponent_two),
        } == {1184, 1185}


class TestVerdictToRating:
    """What each panel outcome does to the ratings."""

    async def test_a_majority_for_b_moves_rating_to_b(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.B, Vote.B, Vote.A]
        )
        change = await _settle_in_own_session(session_maker, battle_id, token)
        assert change is not None and change.applied is True
        assert await _elo(session_maker, agent_b) == DEFAULT_ELO + K_FACTOR // 2
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO - K_FACTOR // 2

    async def test_no_quorum_completes_the_battle_without_touching_rating(
        self, session_maker, db_session, task_id
    ) -> None:
        # All three replicates errored. Nobody won; inventing a winner here is
        # the single thing settlement must never do.
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.ERROR, Vote.ERROR, Vote.ERROR]
        )
        change = await _settle_in_own_session(session_maker, battle_id, token)

        assert change is not None
        assert change.applied is False
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO
        assert await _elo(session_maker, agent_b) == DEFAULT_ELO
        assert await _counters(session_maker, agent_a) == (0, 0, 0)

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"  # completed, but unrated
        assert battle["winner"] is None
        assert "no quorum" in battle["verdict_reason"]

    async def test_abstentions_do_not_reach_quorum_and_do_not_mint_tie_elo(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, _, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.ABSTAIN, Vote.ABSTAIN, Vote.ABSTAIN]
        )
        change = await _settle_in_own_session(session_maker, battle_id, token)
        assert change is not None and change.applied is False
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO
        assert await _counters(session_maker, agent_a) == (0, 0, 0)

    async def test_a_genuine_tie_rates_and_bumps_the_tie_counter(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.TIE, Vote.TIE, Vote.TIE], elo_a=1200, elo_b=1600
        )
        change = await _settle_in_own_session(session_maker, battle_id, token)

        assert change is not None and change.applied is True
        # A draw against a 400-point favourite is a good result for the underdog.
        assert await _elo(session_maker, agent_a) > 1200
        assert await _elo(session_maker, agent_b) < 1600
        assert await _counters(session_maker, agent_a) == (0, 0, 1)
        assert await _counters(session_maker, agent_b) == (0, 0, 1)


class TestSelfPlay:
    """Same owner on both sides: allowed, recorded, never rated."""

    async def test_same_owner_self_play_completes_without_rating_or_counters(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A], same_owner=True
        )
        change = await _settle_in_own_session(session_maker, battle_id, token)

        assert change is not None
        assert change.applied is False
        # Without this an owner farms rating against themselves for the price
        # of inference.
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO
        assert await _elo(session_maker, agent_b) == DEFAULT_ELO
        assert await _counters(session_maker, agent_a) == (0, 0, 0)

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        # The verdict is still recorded honestly — the battle happened.
        assert battle["status"] == "completed"
        assert battle["winner"] == "a"
        assert "self-play" in battle["verdict_reason"]


class TestDeadlineClosure:
    """The silent fighter gets a synthetic answer, not a retroactive abort."""

    async def test_a_silent_fighter_receives_a_truncated_final_submission(
        self, session_maker, db_session, task_id
    ) -> None:
        repo = BattleRepository(db_session)
        events = AgentEventRepository(db_session)
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)

        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        await repo._mark_accepted(battle_id)
        await repo.reserve_both(battle_id, agent_a, agent_b, 600)
        ev_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
        ev_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
        row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
        await repo._mark_queued(battle_id, row["readiness_generation"])
        token = str(uuid.uuid4())
        await repo._mark_running(battle_id, token, 600)

        # Only A answers. B never speaks.
        await repo.add_submission(battle_id, Side.A, 1, "A's answer", is_final=True)
        # Age the battle so its wall clock has genuinely run out, rather than
        # waiting out a real 600s limit. The WHOLE timestamp chain moves, not
        # just the deadline: V66 enforces challenged_at <= queued_at <=
        # started_at < deadline_at, so an expired battle must still be a
        # coherent one. (Rewriting only deadline_at trips
        # battle_deadline_after_start; rewriting only started_at trips
        # battle_started_after_queued — the schema is right and would reject
        # the incoherent row this test would otherwise have asserted against.)
        await db_session.execute(
            text(
                "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                "queued_at = NOW() - INTERVAL '20 minutes', "
                "started_at = NOW() - INTERVAL '10 minutes', "
                "deadline_at = NOW() - INTERVAL '1 second' WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await db_session.commit()

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            assert await runner.close_deadline(battle_id, token) is True

        async with session_maker() as session:
            repo2 = BattleRepository(session)
            submissions = await repo2.list_submissions(battle_id)
            battle = await repo2.get(battle_id)

        finals = {str(s["side"]): s for s in submissions if s["is_final"]}
        assert set(finals) == {"a", "b"}
        assert finals["a"]["content"] == "A's answer"
        assert finals["a"]["truncated"] is False
        # B's silence is a real, judgeable submission — not an abort.
        assert finals["b"]["content"] is None
        assert finals["b"]["truncated"] is True
        assert finals["b"]["error"] == "no submission before deadline"
        assert battle["status"] == "judging"

    async def test_closing_a_deadline_twice_does_not_duplicate_submissions(
        self, session_maker, db_session, task_id
    ) -> None:
        # A restarted reconciler must reproduce the same state, not a second
        # synthetic answer — the partial unique index is what guarantees it.
        battle_id, _, _, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            assert await runner.close_deadline(battle_id, token) is False  # already judging

        async with session_maker() as session:
            submissions = await BattleRepository(session).list_submissions(battle_id)
        assert len([s for s in submissions if s["is_final"]]) == 2


class TestTheWholeChain:
    """The test whose absence let four dead links reach the end of the build.

    There were 153 tests on the parts and none on whether they were connected.
    BattleService.arm_readiness, dispatch_ready_checks, try_queue and
    start_if_still_eligible were each written, covered and mutation-proven — and
    each had zero callers. A battle could be created, accepted and acked, and
    then nothing happened, forever. Every unit test passed the entire time.

    So this asserts the shaft, not the parts: one battle walks challenge ->
    accept -> reserve -> ready-check dispatched -> ACK -> queued -> running ->
    judging, driven ONLY by reconcile_once. Judges are mocked; the pipeline is
    not.
    """

    async def test_a_battle_walks_the_whole_chain_driven_only_by_the_reconciler(
        self, session_maker, db_session, task_id
    ) -> None:
        repo = BattleRepository(db_session)
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)
        # Both fighters must be opted in and eligible, or admission refuses them.
        await db_session.execute(
            text(
                "UPDATE agents SET available_for_battles = TRUE, is_active = TRUE, "
                "owner_user_id = CAST(:o AS UUID) WHERE id = CAST(:a AS UUID)"
            ),
            {"o": owner_a, "a": agent_a},
        )
        await db_session.execute(
            text(
                "UPDATE agents SET available_for_battles = TRUE, is_active = TRUE, "
                "owner_user_id = CAST(:o AS UUID) WHERE id = CAST(:a AS UUID)"
            ),
            {"o": owner_b, "a": agent_b},
        )

        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        assert await repo._mark_accepted(battle_id) is not None
        await db_session.commit()

        # dispatch_existing is patched, not the row creation: this test asserts
        # the durable agent_events rows exist, which is the fact that matters —
        # transport is best-effort and has its own tests. Without the patch the
        # test would hit real Redis/webhooks and prove only that CI has no network.
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None,
            provider={"api_key": "unused", "base_url": "http://unused"},
        )

        # -- pass 1: accepted -> reserved, and the ready-checks GO OUT ---------
        with _no_transport():
            counts = await drive()
        assert counts["armed"] == 1, counts

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "reserved"

        # The link nobody noticed was missing: without dispatch_ready_checks a
        # fighter is never told to ack, so the chain dies here silently.
        events = await _ready_checks(session_maker, battle_id, (agent_a, agent_b))
        assert len(events) == 2, "each fighter gets exactly one ready-check"
        assert {str(e["target_agent_id"]) for e in events} == {agent_a, agent_b}

        # -- pass 2: still reserved, because nobody acked ----------------------
        with _no_transport():
            counts = await drive()
        assert counts["queued"] == 0
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "reserved"

        # -- both fighters ack -------------------------------------------------
        async with session_maker() as session:
            event_repo = AgentEventRepository(session)
            for agent_id, event_key in (
                (agent_a, "ready_check_event_id_a"),
                (agent_b, "ready_check_event_id_b"),
            ):
                acked = await event_repo.mark_acked(agent_id, [str(battle[event_key])])
                assert len(acked) == 1
            await session.commit()

        # -- pass 3: reserved -> queued -> running, in ONE pass ----------------
        # The phases run in order within a single pass, so a battle that is ready
        # advances as far as it legitimately can per tick rather than waiting a
        # tick per link. Both counters are asserted because each proves its own
        # link fired — which is what makes a missing branch fail at its own step.
        with _no_transport():
            counts = await drive()
        assert counts["queued"] == 1, counts
        assert counts["started"] == 1, counts

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "running"
        assert battle["started_at"] is not None
        assert battle["deadline_at"] is not None
        assert battle["deadline_at"] > battle["started_at"]

        turns = await _turn_events(session_maker, (agent_a, agent_b))
        assert len(turns) == 2, "the fighters must be told to fight"
        assert {str(t["target_agent_id"]) for t in turns} == {agent_a, agent_b}
        # The turn expires with the battle, not on the 32400s default.
        assert all(t["expires_at"] <= battle["deadline_at"] for t in turns)

        # -- the clock runs out, then: running -> judging ----------------------
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                    "queued_at = NOW() - INTERVAL '20 minutes', "
                    "started_at = NOW() - INTERVAL '10 minutes', "
                    "deadline_at = NOW() - INTERVAL '1 second', "
                    # Free the row for the next pass to claim. BOTH columns:
                    # V66's battle_lease_token_has_expiry forbids a token with
                    # no expiry, so clearing only one is not a legal state.
                    "lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        with _no_transport(), patch(
            "app.services.battle_runner.BattleRunner.run_judge_panel",
            AsyncMock(return_value=[]),
        ):
            counts = await drive()
        assert counts["judged"] == 1, counts

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
            submissions = await BattleRepository(session).list_submissions(battle_id)
        # Neither fighter answered, so each gets a synthetic truncated final and
        # the battle is judged on silence rather than aborted.
        assert battle["status"] in ("judging", "completed")
        assert len([s for s in submissions if s["is_final"]]) == 2
        assert all(s["truncated"] for s in submissions)


@contextmanager
def _no_transport():
    """Silence BOTH outbound seams. The durable rows are the fact under test.

    Two patches, because each module imported dispatch_existing into its own
    namespace: patching one leaves the other making real network calls.
    """
    queued = AsyncMock(return_value=DeliveryResult.QUEUED)
    with (
        patch("app.services.battle_service.dispatch_existing", queued),
        patch("app.services.battle_runner.dispatch_existing", queued),
    ):
        yield


async def _ready_checks(session_maker, battle_id: str, agents: tuple[str, str]) -> list[dict]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT * FROM agent_events WHERE type = 'battle_ready_check' "
                "AND target_agent_id IN (CAST(:a AS UUID), CAST(:b AS UUID))"
            ),
            {"a": agents[0], "b": agents[1]},
        )
        return [dict(r) for r in rows.mappings()]


async def _turn_events(session_maker, agents: tuple[str, str]) -> list[dict]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT * FROM agent_events WHERE type = 'battle_turn' "
                "AND target_agent_id IN (CAST(:a AS UUID), CAST(:b AS UUID))"
            ),
            {"a": agents[0], "b": agents[1]},
        )
        return [dict(r) for r in rows.mappings()]


# ---------------------------------------------------------------------------
# Review-fix regression suites (F1–F7). Each asserts a rejection/behaviour that
# a revert of the fix would flip, so the test dies with the fix.
# ---------------------------------------------------------------------------


async def _battle_running(session, task_id: str) -> tuple[str, str, str, str]:
    """Drive a battle to 'running' via the real machine. Returns ids + token.

    Stops at 'running' (not 'judging'), holding ``token`` as its row lease — the
    shape a reconciler claim produces, which close_deadline's release path needs.
    deadline_at is NOW()+600, i.e. genuinely in the future.
    """
    repo = BattleRepository(session)
    events = AgentEventRepository(session)
    owner_a, owner_b = await _new_owner(session), await _new_owner(session)
    agent_a, agent_b = await _new_agent(session), await _new_agent(session)
    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_a,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_b,
    )
    await repo._mark_accepted(battle_id)
    await repo.reserve_both(battle_id, agent_a, agent_b, 600)
    ev_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    ev_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
    await repo._mark_queued(battle_id, row["readiness_generation"])
    token = str(uuid.uuid4())
    await repo._mark_running(battle_id, token, 600)
    await session.commit()
    return battle_id, agent_a, agent_b, token


async def _build_queued_battle(
    session, task_id: str, reserve_ttl: int = 90
) -> tuple[str, str, str]:
    """Drive to 'queued' with ELIGIBLE, reserved fighters. Returns ids.

    Fighters are opted in and owned so start_if_still_eligible's re-check passes.
    The reservation ttl is short (90s, the real RESERVATION_SECONDS) so that a
    started battle whose reservations were NOT extended would leave a hold that
    lapses before its deadline — which is exactly what F5 fixes.
    """
    repo = BattleRepository(session)
    events = AgentEventRepository(session)
    owner_a, owner_b = await _new_owner(session), await _new_owner(session)
    agent_a, agent_b = await _new_agent(session), await _new_agent(session)
    for agent, owner in ((agent_a, owner_a), (agent_b, owner_b)):
        await session.execute(
            text(
                "UPDATE agents SET available_for_battles = TRUE, is_active = TRUE, "
                "owner_user_id = CAST(:o AS UUID) WHERE id = CAST(:a AS UUID)"
            ),
            {"o": owner, "a": agent},
        )
    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_a,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_b,
    )
    await repo._mark_accepted(battle_id)
    await repo.reserve_both(battle_id, agent_a, agent_b, reserve_ttl)
    ev_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    ev_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
    await repo._mark_queued(battle_id, row["readiness_generation"])
    await session.commit()
    return battle_id, agent_a, agent_b


async def _fake_half(**kwargs) -> JudgeRunResult:
    """A stand-in for _run_one_half that answers without any HTTP call."""
    return JudgeRunResult(presented_order=kwargs["order"], vote=Vote.A, confidence=0.9)


class TestProviderOutageDoesNotFreezeLifecycle:
    """The blocker: a judge-provider outage must not freeze the WHOLE lifecycle.

    reconcile_once drives BOTH the free DB-only phases (arm accepted->reserved,
    admit reserved->queued, start queued->running, close_deadline
    running->judging) AND the reaper (expire challenges, release stranded
    reservations) — none of which need the judge provider. Only the judging
    phase spends provider calls. Before the fix, run_once resolved the provider
    FIRST and returned the entire pass when it was None, so a z.ai key that was
    unset/rotated/geo-blocked (this platform's active failure mode) silently
    stalled every battle at every stage and stopped all cleanup, not just
    scoring.

    This drives ONE reconcile pass with provider=None and asserts every free
    phase still advances and the reaper still runs. A battle already in 'judging'
    stays there (judging genuinely needs the model) — it waits, it does not error
    or abort.

    MUTATION: restore `if provider is None: return` at the top of reconcile_once
    (equivalently run_once's early return) and this test fails — nothing advances
    and nothing is reaped.
    """

    async def test_free_phases_and_reaper_run_without_a_provider(
        self, session_maker, db_session, task_id
    ) -> None:
        repo = BattleRepository(db_session)
        events = AgentEventRepository(db_session)

        async def _opt_in(agent: str, owner: str) -> None:
            await db_session.execute(
                text(
                    "UPDATE agents SET available_for_battles = TRUE, is_active = TRUE, "
                    "owner_user_id = CAST(:o AS UUID) WHERE id = CAST(:a AS UUID)"
                ),
                {"o": owner, "a": agent},
            )

        # -- 1. an ACCEPTED battle, ready to arm (accepted -> reserved) --------
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)
        await _opt_in(agent_a, owner_a)
        await _opt_in(agent_b, owner_b)
        accepted_id = await repo._create_battle(
            task_id=task_id, agent_a_id=agent_a, agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600, agent_b_id=agent_b, agent_b_owner_snapshot=owner_b,
        )
        assert await repo._mark_accepted(accepted_id) is not None

        # -- 2. a RESERVED battle with both fighters acked (reserved -> queued) -
        r_owner_a, r_owner_b = await _new_owner(db_session), await _new_owner(db_session)
        r_agent_a, r_agent_b = await _new_agent(db_session), await _new_agent(db_session)
        await _opt_in(r_agent_a, r_owner_a)
        await _opt_in(r_agent_b, r_owner_b)
        reserved_id = await repo._create_battle(
            task_id=task_id, agent_a_id=r_agent_a, agent_a_owner_snapshot=r_owner_a,
            challenge_ttl_seconds=3600, agent_b_id=r_agent_b, agent_b_owner_snapshot=r_owner_b,
        )
        assert await repo._mark_accepted(reserved_id) is not None
        assert len(await repo.reserve_both(reserved_id, r_agent_a, r_agent_b, 600)) == 2
        ev_a = await events.create(r_agent_a, "battle_ready_check", {}, ttl_seconds=60)
        ev_b = await events.create(r_agent_b, "battle_ready_check", {}, ttl_seconds=60)
        assert await repo.arm_readiness(reserved_id, ev_a, ev_b, 60) is not None
        # Both fighters ack, so admit_to_queue's precondition holds this pass.
        assert len(await events.mark_acked(r_agent_a, [str(ev_a)])) == 1
        assert len(await events.mark_acked(r_agent_b, [str(ev_b)])) == 1
        await db_session.commit()
        async with session_maker() as s:
            assert (await BattleRepository(s).get(reserved_id))["status"] == "reserved"

        # -- 3. a QUEUED battle, eligible (queued -> running) ------------------
        queued_id, _, _ = await _build_queued_battle(db_session, task_id)

        # -- 4. a RUNNING battle past its deadline (running -> judging, FREE) ---
        running_id, _, _, _ = await _battle_running(db_session, task_id)
        async with session_maker() as s:
            await s.execute(
                text(
                    "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                    "queued_at = NOW() - INTERVAL '20 minutes', "
                    "started_at = NOW() - INTERVAL '10 minutes', "
                    "deadline_at = NOW() - INTERVAL '1 second', "
                    "lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": running_id},
            )
            await s.commit()

        # -- 5. a battle already in JUDGING — must WAIT, not error/abort -------
        judging_id, _, _, _ = await _battle_in_judging(db_session, task_id, votes=[Vote.A])

        # -- 6. an expired challenge (reaper -> expired) -----------------------
        exp_owner = await _new_owner(db_session)
        exp_a, exp_b = await _new_agent(db_session), await _new_agent(db_session)
        expired_id = await repo._create_battle(
            task_id=task_id, agent_a_id=exp_a, agent_a_owner_snapshot=exp_owner,
            challenge_ttl_seconds=3600, agent_b_id=exp_b, agent_b_owner_snapshot=exp_owner,
        )
        await db_session.execute(
            text(
                "UPDATE battles SET challenge_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"b": expired_id},
        )

        # -- 7. a stranded reservation on a non-live battle (reaper releases) --
        res_owner = await _new_owner(db_session)
        res_a, res_b = await _new_agent(db_session), await _new_agent(db_session)
        stranded_res_id = await repo._create_battle(
            task_id=task_id, agent_a_id=res_a, agent_a_owner_snapshot=res_owner,
            challenge_ttl_seconds=3600, agent_b_id=res_b, agent_b_owner_snapshot=res_owner,
        )
        await db_session.execute(
            text(
                "INSERT INTO battle_reservations (agent_id, battle_id, reserved_until, created_at) "
                "VALUES (CAST(:a AS UUID), CAST(:b AS UUID), NOW() - INTERVAL '1 second', "
                "NOW() - INTERVAL '2 minutes')"
            ),
            {"a": res_a, "b": stranded_res_id},
        )
        await db_session.commit()

        # -- ONE reconcile pass, with NO provider ------------------------------
        # gate=None is safe: only run_judge_panel touches the gate, and the
        # judging phase is exactly what a None provider skips. _no_transport
        # silences the arm/start ready-check + turn dispatch.
        with _no_transport():
            counts = await reconcile_once(
                session_factory=session_maker, gate=None, provider=None
            )

        # Free lifecycle advanced at every stage, with NO provider:
        assert counts["armed"] >= 1, counts
        assert counts["queued"] >= 1, counts
        assert counts["started"] >= 2, counts  # the reserved AND the queued battle
        assert counts["judged"] >= 1, counts  # close_deadline: running -> judging
        # Reaper ran:
        assert counts["expired"] >= 1, counts
        assert counts["reservations_reaped"] >= 1, counts
        # Judging phase was skipped, so nothing settled this pass:
        assert counts["settled"] == 0, counts

        async with session_maker() as s:
            get = BattleRepository(s).get
            assert (await get(accepted_id))["status"] == "reserved"
            assert (await get(reserved_id))["status"] == "running"  # admitted then started
            assert (await get(queued_id))["status"] == "running"
            assert (await get(running_id))["status"] == "judging"  # closed, awaiting a judge
            assert (await get(expired_id))["status"] == "expired"
            # The battle already in judging simply waits for a provider — it is
            # neither errored nor aborted.
            assert (await get(judging_id))["status"] == "judging"

        # The stranded reservation was released.
        assert await _reservation_count(session_maker, stranded_res_id) == 0


class TestDeadlineGate:
    """F1: a running battle is closed only when it is actually finished."""

    async def test_running_battle_before_deadline_is_not_closed_early(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_running(db_session, task_id)

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            assert await runner.close_deadline(battle_id, token) is False

        async with session_maker() as session:
            repo = BattleRepository(session)
            battle = await repo.get(battle_id)
            submissions = await repo.list_submissions(battle_id)

        # The battle keeps running, NOTHING synthetic was written, and the claim
        # was released (with its attempt undone) rather than burned.
        assert battle["status"] == "running"
        assert submissions == []
        assert battle["lease_token"] is None
        assert battle["lease_attempt_count"] == 0

    async def test_running_battle_past_deadline_closes_with_two_silent_finals(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_running(db_session, task_id)

        # Age the whole timestamp chain and re-arm the lease (a fresh claim).
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                    "queued_at = NOW() - INTERVAL '20 minutes', "
                    "started_at = NOW() - INTERVAL '10 minutes', "
                    "deadline_at = NOW() - INTERVAL '1 second', "
                    "lease_token = CAST(:t AS UUID), "
                    "lease_expires_at = NOW() + INTERVAL '5 minutes' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"t": token, "b": battle_id},
            )
            await session.commit()

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            assert await runner.close_deadline(battle_id, token) is True

        async with session_maker() as session:
            repo = BattleRepository(session)
            battle = await repo.get(battle_id)
            finals = [s for s in await repo.list_submissions(battle_id) if s["is_final"]]

        assert battle["status"] == "judging"
        assert len(finals) == 2
        assert all(s["truncated"] for s in finals)


class TestJudgingResume:
    """F2: a battle stranded in 'judging' by a crash is completed by reconcile."""

    async def test_reconcile_completes_a_battle_stranded_in_judging(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, _ = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.B]
        )
        # Simulate the crash: the lease has lapsed, the votes are all recorded,
        # and nothing has settled the battle.
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None,
            provider={"api_key": "unused", "base_url": "http://unused"},
        )
        with _no_transport(), patch(
            "app.services.battle_runner.BattleRunner.run_judge_panel",
            AsyncMock(return_value=[]),
        ):
            counts = await drive()

        assert counts["settled"] >= 1, counts
        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        # Without the judging-resume phase this stays 'judging' forever.
        assert battle["status"] == "completed"
        assert battle["winner"] == "a"


class TestJudgePanelLeaseRenewal:
    """F3: run_judge_panel renews the battle lease and aborts if it is lost."""

    async def test_panel_renews_the_lease_after_every_half(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])
        renew_spy = AsyncMock(return_value=True)

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch.object(runner, "_run_one_half", side_effect=_fake_half), patch.object(
                runner.repo, "renew_battle_lease", renew_spy
            ):
                await runner.run_judge_panel(battle_id, "k", "http://u", token)

        # Three replicates x two halves = six renewals.
        assert renew_spy.await_count == 6
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert len(judgements) == 3

    async def test_panel_aborts_when_a_renewal_reports_the_lease_lost(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch.object(runner, "_run_one_half", side_effect=_fake_half), patch.object(
                runner.repo, "renew_battle_lease", AsyncMock(return_value=False)
            ):
                result = await runner.run_judge_panel(battle_id, "k", "http://u", token)

        # Aborted after the first half, before any vote was persisted.
        assert result == []
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert judgements == []


class TestTransientJudgeErrorNotFrozen:
    """F4: a transient transport error is not frozen as an error vote."""

    async def test_a_transient_error_leaves_the_replicate_reclaimable(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.call_judge_model",
                AsyncMock(side_effect=JudgeTransportError("throttled 1302")),
            ):
                await runner.run_judge_panel(battle_id, "k", "http://u", token)

        async with session_maker() as session:
            repo = BattleRepository(session)
            judgements = await repo.list_judgements(battle_id)
            runs = await repo.list_judge_runs(battle_id)

        # No collapsed vote was written — the old ON CONFLICT DO NOTHING would have
        # frozen an 'error' here and blocked every correct re-run.
        assert judgements == []
        # The raw runs remain 'running' with attempts left, i.e. reclaimable.
        assert runs and all(
            r["status"] == "running" and r["attempt_count"] == 1 for r in runs
        )

    async def test_a_later_pass_records_the_real_vote(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.call_judge_model",
                AsyncMock(side_effect=JudgeTransportError("1302")),
            ):
                await runner.run_judge_panel(battle_id, "k", "http://u", token)

        # The run leases lapse (a real later pass is > JUDGE_RUN_LEASE_SECONDS on).
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battle_judge_runs SET lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE battle_id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        valid = (
            '{"vote": "submission_alpha", "confidence": 0.9, "reasoning": "ok", '
            '"scores": {"correctness": 1.0}}'
        )
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.call_judge_model", AsyncMock(return_value=valid)
            ):
                await runner.run_judge_panel(battle_id, "k", "http://u", token)

        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert len(judgements) == 3
        # Real votes, never the frozen 'error' the first pass would otherwise leave.
        assert all(j["vote"] != "error" for j in judgements)

    async def test_an_exhausted_budget_collapses_to_error_and_settle_proceeds(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, _, token = await _battle_in_judging(db_session, task_id, votes=[])

        # Drive the panel until every run's attempt budget is spent, aging leases
        # between passes so the next pass can reclaim.
        for _ in range(JUDGE_RUN_MAX_ATTEMPTS):
            async with session_maker() as session:
                runner = BattleRunner(session, gate=None)
                with patch(
                    "app.services.battle_runner.call_judge_model",
                    AsyncMock(side_effect=JudgeTransportError("1302")),
                ):
                    await runner.run_judge_panel(battle_id, "k", "http://u", token)
            async with session_maker() as session:
                await session.execute(
                    text(
                        "UPDATE battle_judge_runs "
                        "SET lease_expires_at = NOW() - INTERVAL '1 second' "
                        "WHERE battle_id = CAST(:b AS UUID) AND status <> 'completed'"
                    ),
                    {"b": battle_id},
                )
                await session.commit()

        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert len(judgements) == 3
        assert all(j["vote"] == "error" for j in judgements)

        # An exhausted panel has a terminal (no-quorum) verdict, so settle fires.
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET lease_token = CAST(:t AS UUID), "
                    "lease_expires_at = NOW() + INTERVAL '5 minutes' WHERE id = CAST(:b AS UUID)"
                ),
                {"t": token, "b": battle_id},
            )
            await session.commit()
        change = await _settle_in_own_session(session_maker, battle_id, token)
        assert change is not None and change.applied is False
        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"
        assert battle["winner"] is None
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO


class TestReservationHeldThroughDeadline:
    """F5: a started fighter's reservation covers the whole battle."""

    async def test_start_extends_reservations_past_the_deadline(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _ = await _build_queued_battle(db_session, task_id, reserve_ttl=90)
        token = str(uuid.uuid4())

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with _no_transport():
                assert await runner.start_queued({"id": battle_id}, token) is True

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
            rows = await session.execute(
                text(
                    "SELECT reserved_until FROM battle_reservations "
                    "WHERE battle_id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            held = [r[0] for r in rows.fetchall()]

        assert len(held) == 2
        # Without F5 the reservation stays at 90s, well short of the 600s deadline.
        assert all(until >= battle["deadline_at"] for until in held)

    async def test_a_started_fighter_cannot_be_double_booked(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, agent_b = await _build_queued_battle(
            db_session, task_id, reserve_ttl=90
        )
        token = str(uuid.uuid4())
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with _no_transport():
                assert await runner.start_queued({"id": battle_id}, token) is True

        async with session_maker() as session:
            repo = BattleRepository(session)
            other = await repo._create_battle(
                task_id=task_id,
                agent_a_id=agent_a,
                agent_a_owner_snapshot=await _new_owner(session),
                challenge_ttl_seconds=3600,
                agent_b_id=agent_b,
                agent_b_owner_snapshot=await _new_owner(session),
            )
            with pytest.raises(ReservationConflictError):
                await repo.reserve_both(other, agent_a, agent_b, 90)


class TestAddSubmissionAtomicity:
    """F6: the running + deadline + no-prior-final guards live inside the INSERT."""

    async def test_a_turn_against_a_judging_battle_is_rejected(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, _ = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        async with session_maker() as session:
            repo = BattleRepository(session)
            accepted = await repo.add_submission(battle_id, Side.A, 5, "late", is_final=False)
            await session.commit()
        assert accepted is False

    async def test_a_turn_against_a_non_running_battle_is_rejected(
        self, session_maker, db_session, task_id
    ) -> None:
        # A queued battle has no submissions, so only the status guard can reject.
        battle_id, agent_a, _ = await _build_queued_battle(db_session, task_id)
        async with session_maker() as session:
            repo = BattleRepository(session)
            accepted = await repo.add_submission(battle_id, Side.A, 1, "early", is_final=False)
            await session.commit()
        assert accepted is False

    async def test_a_non_final_turn_after_a_final_is_rejected(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, _ = await _battle_running(db_session, task_id)
        async with session_maker() as session:
            repo = BattleRepository(session)
            assert await repo.add_submission(battle_id, Side.A, 1, "final", is_final=True) is True
            # seq_no 2 > 1 slips past monotonicity; the no-prior-final rule stops it.
            assert (
                await repo.add_submission(battle_id, Side.A, 2, "more", is_final=False) is False
            )
            await session.commit()

    async def test_the_deadline_binds_fighters_but_not_the_synthetic_final(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, _ = await _battle_running(db_session, task_id)
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                    "queued_at = NOW() - INTERVAL '20 minutes', "
                    "started_at = NOW() - INTERVAL '10 minutes', "
                    "deadline_at = NOW() - INTERVAL '1 second' WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()
        async with session_maker() as session:
            repo = BattleRepository(session)
            # A fighter cannot land a turn past the wall clock.
            assert await repo.add_submission(battle_id, Side.A, 1, "late", is_final=True) is False
            # The reconciler's synthetic final still lands (enforce_deadline=False).
            assert (
                await repo.add_submission(
                    battle_id, Side.B, 9999, None, is_final=True, truncated=True,
                    error="no submission before deadline", enforce_deadline=False,
                )
                is True
            )
            await session.commit()


class TestReaper:
    """F7: mark_expired / mark_aborted / reservation reaping finally have a caller."""

    async def test_reaper_expires_a_challenge_past_its_deadline(
        self, session_maker, db_session, task_id
    ) -> None:
        repo = BattleRepository(db_session)
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)
        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        await db_session.execute(
            text(
                "UPDATE battles SET challenge_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await db_session.commit()

        await reap_once(session_maker)
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "expired"

        # Idempotent: a terminal row is skipped by mark_expired's CAS.
        await reap_once(session_maker)
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "expired"

    async def test_reaper_aborts_a_battle_that_exhausted_its_claim_budget(
        self, session_maker, db_session, task_id
    ) -> None:
        repo = BattleRepository(db_session)
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)
        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        await repo._mark_accepted(battle_id)
        await db_session.execute(
            text(
                "UPDATE battles SET lease_attempt_count = :c WHERE id = CAST(:b AS UUID)"
            ),
            {"c": POLL_MAX_ATTEMPTS, "b": battle_id},
        )
        await db_session.commit()

        await reap_once(session_maker)
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "aborted"

    async def test_reaper_deletes_an_expired_reservation(
        self, session_maker, db_session, task_id
    ) -> None:
        repo = BattleRepository(db_session)
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)
        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        await db_session.execute(
            text(
                "INSERT INTO battle_reservations (agent_id, battle_id, reserved_until, created_at) "
                "VALUES (CAST(:a AS UUID), CAST(:b AS UUID), NOW() - INTERVAL '1 second', "
                "NOW() - INTERVAL '2 minutes')"
            ),
            {"a": agent_a, "b": battle_id},
        )
        await db_session.commit()

        await reap_once(session_maker)
        async with session_maker() as session:
            held = await session.execute(
                text(
                    "SELECT COUNT(*) FROM battle_reservations WHERE agent_id = CAST(:a AS UUID)"
                ),
                {"a": agent_a},
            )
        assert held.scalar_one() == 0

    async def test_reaper_leaves_a_terminal_battle_untouched(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        assert await _settle_in_own_session(session_maker, battle_id, token) is not None

        await reap_once(session_maker)
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "completed"


async def _reservation_count(session_maker, battle_id: str) -> int:
    async with session_maker() as session:
        row = await session.execute(
            text(
                "SELECT COUNT(*) FROM battle_reservations WHERE battle_id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        return int(row.scalar_one())


class TestStrandedJudgingEscapeHatch:
    """NEW-1: a judging battle whose attempt budget is spent must reach terminal.

    The judging-resume phase stops claiming a battle once lease_attempt_count hits
    RUNNING_MAX_ATTEMPTS, so a battle whose panel keeps throttling would otherwise
    sit in 'judging' forever, unclaimable, with both fighters pinned. The reaper's
    escape hatch collapses its still-open replicates to error votes and settles to
    a no-quorum verdict — completed and UNRATED, never a minted tie.

    MUTATION: delete the ``stranded_ids`` loop in reap_once and this test FAILS —
    the battle stays 'judging' and the reservations stay held.
    """

    async def test_a_spent_budget_judging_battle_is_settled_to_no_quorum(
        self, session_maker, db_session, task_id
    ) -> None:
        # No judgements yet: every replicate is still open when the budget dies.
        battle_id, agent_a, agent_b, _ = await _battle_in_judging(
            db_session, task_id, votes=[]
        )
        # Spend the judging budget and lapse the lease — the exact stranded shape.
        await db_session.execute(
            text(
                "UPDATE battles SET lease_attempt_count = :c, "
                "lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"c": RUNNING_MAX_ATTEMPTS, "b": battle_id},
        )
        await db_session.commit()

        assert await _reservation_count(session_maker, battle_id) == 2
        elo_a_before = await _elo(session_maker, agent_a)
        elo_b_before = await _elo(session_maker, agent_b)

        # A provider is present, so the escape hatch is allowed to fire (it makes
        # no network call — collapse-to-error + settle are DB-only).
        counts = await reap_once(session_maker, {"api_key": "unused", "base_url": "http://unused"})

        assert counts["stranded_settled"] == 1
        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"
        # No quorum: winner is NULL and no Elo moved — a broken judge mints nothing.
        assert battle["winner"] is None
        assert await _elo(session_maker, agent_a) == elo_a_before
        assert await _elo(session_maker, agent_b) == elo_b_before
        # Fighters are freed once the battle finalized.
        assert await _reservation_count(session_maker, battle_id) == 0

    async def test_escape_hatch_is_idempotent(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, _ = await _battle_in_judging(db_session, task_id, votes=[])
        await db_session.execute(
            text(
                "UPDATE battles SET lease_attempt_count = :c, "
                "lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"c": RUNNING_MAX_ATTEMPTS, "b": battle_id},
        )
        await db_session.commit()

        stub = {"api_key": "unused", "base_url": "http://unused"}
        assert (await reap_once(session_maker, stub))["stranded_settled"] == 1
        # Second pass: the row is 'completed' now, so the finder skips it.
        assert (await reap_once(session_maker, stub))["stranded_settled"] == 0
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "completed"

    async def test_the_escape_hatch_waits_for_a_provider_during_an_outage(
        self, session_maker, db_session, task_id
    ) -> None:
        """A spent-budget judging battle must NOT be no-quorum-settled mid-outage.

        The escape hatch mints an honest no-quorum only for a panel that genuinely
        exhausted its budget WITH a working provider. During a provider outage the
        SAME battle must WAIT — a later provider-backed pass could still judge it.
        Finalizing it unrated while the model is merely unreachable throws away a
        battle that could still get a real verdict.

        MUTATION: drop the provider gate on the stranded-judging loop in reap_once
        and the outage-waits assertion below fails — the battle gets settled to
        no-quorum during the outage.
        """
        # Exactly the stranded shape: no votes, budget spent, lease lapsed.
        battle_id, agent_a, agent_b, _ = await _battle_in_judging(
            db_session, task_id, votes=[]
        )
        await db_session.execute(
            text(
                "UPDATE battles SET lease_attempt_count = :c, "
                "lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"c": RUNNING_MAX_ATTEMPTS, "b": battle_id},
        )
        await db_session.commit()

        elo_a_before = await _elo(session_maker, agent_a)
        elo_b_before = await _elo(session_maker, agent_b)

        # -- OUTAGE: provider=None -> the escape hatch must NOT fire -----------
        counts = await reap_once(session_maker, None)
        assert counts["stranded_settled"] == 0, counts
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "judging"
        # It waits: not completed, not settled, no Elo moved, fighters still held.
        assert await _elo(session_maker, agent_a) == elo_a_before
        assert await _elo(session_maker, agent_b) == elo_b_before
        assert await _reservation_count(session_maker, battle_id) == 2

        # -- RECOVERY: provider present -> the escape hatch finalizes it -------
        counts = await reap_once(
            session_maker, {"api_key": "unused", "base_url": "http://unused"}
        )
        assert counts["stranded_settled"] == 1, counts
        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"
        assert battle["winner"] is None  # no-quorum, unrated
        assert await _elo(session_maker, agent_a) == elo_a_before
        assert await _elo(session_maker, agent_b) == elo_b_before
        assert await _reservation_count(session_maker, battle_id) == 0


class TestReaperSparesLiveBattleReservations:
    """NEW-2: delete_expired_reservations must not free a fighter mid-fight."""

    async def test_a_lapsed_reservation_of_a_judging_battle_survives_reap(
        self, session_maker, db_session, task_id
    ) -> None:
        # A live (judging) battle, NOT stranded — budget was reset by mark_judging.
        battle_id, _, _, _ = await _battle_in_judging(db_session, task_id, votes=[])
        # Its holds have lapsed by wall clock, but the battle is still being
        # judged. created_at is pushed back too, so reserved_until still satisfies
        # the battle_reservation_future check (reserved_until > created_at).
        await db_session.execute(
            text(
                "UPDATE battle_reservations "
                "SET reserved_until = NOW() - INTERVAL '1 second', "
                "    created_at = NOW() - INTERVAL '2 minutes' "
                "WHERE battle_id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await db_session.commit()

        await reap_once(session_maker)

        # MUTATION: revert the NOT EXISTS (running/judging) guard in
        # delete_expired_reservations and this assertion FAILS — the fighters get
        # freed mid-fight and can be double-booked.
        assert await _reservation_count(session_maker, battle_id) == 2
        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "judging"

    async def test_a_lapsed_reservation_of_a_terminal_battle_is_reaped(
        self, session_maker, db_session, task_id
    ) -> None:
        # Same shape, but the battle is already completed — the hold is dead weight.
        battle_id, _, _, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        assert await _settle_in_own_session(session_maker, battle_id, token) is not None
        # finalize already releases reservations, so re-add a stray lapsed one to
        # prove the reaper deletes it once the battle is no longer live.
        await db_session.execute(
            text(
                "INSERT INTO battle_reservations (agent_id, battle_id, reserved_until, created_at) "
                "SELECT agent_a_id, id, NOW() - INTERVAL '1 second', NOW() - INTERVAL '2 minutes' "
                "FROM battles WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await db_session.commit()
        assert await _reservation_count(session_maker, battle_id) == 1

        await reap_once(session_maker)

        assert await _reservation_count(session_maker, battle_id) == 0

    async def test_finalize_releases_the_reservations(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        assert await _reservation_count(session_maker, battle_id) == 2

        assert await _settle_in_own_session(session_maker, battle_id, token) is not None

        assert await _reservation_count(session_maker, battle_id) == 0


class TestReaperRespectsTheBatchBound:
    """NEW-5: each finder is bounded by RECONCILE_BATCH; a backlog drains over passes."""

    async def test_one_pass_expires_at_most_a_batch_then_drains_the_rest(
        self, session_maker, db_session, task_id
    ) -> None:
        overflow = 2
        owner = await _new_owner(db_session)
        repo = BattleRepository(db_session)
        battle_ids: list[str] = []
        for _ in range(RECONCILE_BATCH + overflow):
            agent_a, agent_b = await _new_agent(db_session), await _new_agent(db_session)
            bid = await repo._create_battle(
                task_id=task_id,
                agent_a_id=agent_a,
                agent_a_owner_snapshot=owner,
                challenge_ttl_seconds=3600,
                agent_b_id=agent_b,
                agent_b_owner_snapshot=owner,
            )
            await db_session.execute(
                text(
                    "UPDATE battles SET challenge_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": bid},
            )
            battle_ids.append(bid)
        await db_session.commit()

        # One pass reaps exactly a batch, never the whole backlog.
        first = await reap_once(session_maker)
        assert first["expired"] == RECONCILE_BATCH

        # The remainder drains on the next pass — nothing is stranded by the bound.
        second = await reap_once(session_maker)
        assert second["expired"] == overflow

        async with session_maker() as session:
            get_repo = BattleRepository(session)
            statuses = [(await get_repo.get(b))["status"] for b in battle_ids]
        assert all(s == "expired" for s in statuses)


# ---------------------------------------------------------------------------
# DX round: early-finish lease release (FIX 1), owner notifications (FIX 2)
# and the untested lifecycle branches (FIX 3). Helpers below are additive so
# the suites above are untouched.
# ---------------------------------------------------------------------------


async def _new_eligible_agent(session, owner: str, elo: int = DEFAULT_ELO) -> str:
    """A fighter that satisfies _AGENT_ELIGIBLE_SQL: active, not hosted, owned,
    opted in. Needed by the open-challenge claim path, which re-imposes every
    admission rule against the agent that turns up."""
    aid = await _new_agent(session, elo)
    await session.execute(
        text(
            "UPDATE agents SET available_for_battles = TRUE, is_active = TRUE, "
            "is_hosted = FALSE, owner_user_id = CAST(:o AS UUID) "
            "WHERE id = CAST(:a AS UUID)"
        ),
        {"o": owner, "a": aid},
    )
    return aid


async def _reservation_count(session_maker, battle_id: str) -> int:
    async with session_maker() as session:
        row = await session.execute(
            text("SELECT COUNT(*) FROM battle_reservations WHERE battle_id = CAST(:b AS UUID)"),
            {"b": battle_id},
        )
        return int(row.scalar_one())


async def _live_event_count(session_maker, agents: tuple[str, ...]) -> int:
    """Agent-events still in flight (not past their TTL) for these agents.

    A terminal battle must leave no LIVE obligation behind — a ready-check or
    turn event whose expires_at is still in the future would tell a fighter to
    act on a battle that has ended.
    """
    ids = ", ".join(f"CAST(:a{i} AS UUID)" for i in range(len(agents)))
    params = {f"a{i}": a for i, a in enumerate(agents)}
    async with session_maker() as session:
        row = await session.execute(
            text(
                f"SELECT COUNT(*) FROM agent_events "
                f"WHERE target_agent_id IN ({ids}) AND expires_at > NOW()"
            ),
            params,
        )
        return int(row.scalar_one())


class TestEarlyFinishLeaseRelease:
    """FIX 1: both fighters final -> the running row is claimable AT ONCE.

    Without the release the row keeps a lease running for BATTLE_LEASE_SECONDS
    and the reconciler's running phase (claim_battles_for_reconcile) will not
    touch it until that lapses, so a battle both sides finished in seconds still
    waits out the whole window before it can be judged.
    """

    async def test_both_finals_make_a_running_battle_immediately_claimable(
        self, session_maker, db_session, task_id
    ) -> None:
        async with session_maker() as session:
            battle_id, agent_a, agent_b, _token = await _battle_running(session, task_id)
            repo = BattleRepository(session)
            # The lease is genuinely in the future: _battle_running set it to
            # NOW()+600 via _mark_running, so nothing has lapsed on its own.
            live = await session.execute(
                text(
                    "SELECT lease_expires_at > NOW() FROM battles WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            assert live.scalar_one() is True

            assert await repo.add_submission(battle_id, Side.A, 1, "A", is_final=True)
            assert await repo.add_submission(battle_id, Side.B, 1, "B", is_final=True)
            assert await repo.expire_running_lease_if_both_final(battle_id) is True
            await session.commit()

        # Directly: the row's lease has been retired.
        async with session_maker() as session:
            lapsed = await session.execute(
                text("SELECT lease_expires_at <= NOW() FROM battles WHERE id = CAST(:b AS UUID)"),
                {"b": battle_id},
            )
            assert lapsed.scalar_one() is True

        # End to end: the running phase claims it on the very next tick.
        async with session_maker() as session:
            claimed = await BattleRepository(session).claim_battles_for_reconcile(
                status=BattleStatus.RUNNING,
                lease_token=str(uuid.uuid4()),
                lease_seconds=BATTLE_LEASE_SECONDS,
                limit=RECONCILE_BATCH,
                max_attempts=RUNNING_MAX_ATTEMPTS,
            )
            await session.rollback()
        assert battle_id in {str(b["id"]) for b in claimed}

    async def test_one_final_leaves_the_running_lease_untouched(
        self, session_maker, db_session, task_id
    ) -> None:
        async with session_maker() as session:
            battle_id, agent_a, agent_b, _token = await _battle_running(session, task_id)
            repo = BattleRepository(session)
            assert await repo.add_submission(battle_id, Side.A, 1, "A", is_final=True)
            # Only one side is final: the CAS must not fire.
            assert await repo.expire_running_lease_if_both_final(battle_id) is False
            await session.commit()

        async with session_maker() as session:
            still_live = await session.execute(
                text("SELECT lease_expires_at > NOW() FROM battles WHERE id = CAST(:b AS UUID)"),
                {"b": battle_id},
            )
            assert still_live.scalar_one() is True

        # And the running phase will NOT claim it — the lease has not lapsed.
        async with session_maker() as session:
            claimed = await BattleRepository(session).claim_battles_for_reconcile(
                status=BattleStatus.RUNNING,
                lease_token=str(uuid.uuid4()),
                lease_seconds=BATTLE_LEASE_SECONDS,
                limit=RECONCILE_BATCH,
                max_attempts=RUNNING_MAX_ATTEMPTS,
            )
            await session.rollback()
        assert battle_id not in {str(b["id"]) for b in claimed}

    async def test_a_released_null_lease_is_skipped_without_violating_the_check(
        self, session_maker, db_session, task_id
    ) -> None:
        # A normal pre-deadline reconcile poll releases the running row to
        # lease_token=NULL / lease_expires_at=NULL (release_reconcile_claim).
        # Writing expires_at=NOW() onto that row would violate V66's
        # battle_lease_token_has_expiry CHECK; the `lease_token IS NOT NULL`
        # guard must skip it and return False instead of raising. (Removing the
        # guard turns this into a CheckViolationError.)
        async with session_maker() as session:
            battle_id, _agent_a, _agent_b, _token = await _battle_running(session, task_id)
            repo = BattleRepository(session)
            await session.execute(
                text(
                    "UPDATE battles SET lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            assert await repo.add_submission(battle_id, Side.A, 1, "A", is_final=True)
            assert await repo.add_submission(battle_id, Side.B, 1, "B", is_final=True)
            # Must not raise, and must report "nothing to nudge".
            assert await repo.expire_running_lease_if_both_final(battle_id) is False
            await session.commit()

        # The NULL/NULL running row is already claimable on its own.
        async with session_maker() as session:
            claimed = await BattleRepository(session).claim_battles_for_reconcile(
                status=BattleStatus.RUNNING,
                lease_token=str(uuid.uuid4()),
                lease_seconds=BATTLE_LEASE_SECONDS,
                limit=RECONCILE_BATCH,
                max_attempts=RUNNING_MAX_ATTEMPTS,
            )
            await session.rollback()
        assert battle_id in {str(b["id"]) for b in claimed}


class TestOwnerNotifications:
    """FIX 2: terminal transitions notify owners through the platform's task
    mechanism, best-effort. create_notification_task is the real mechanism (it
    writes a tasks row + pushes on heartbeat); here it is mocked because the
    testcontainers schema carries only the battle tables, and what is under test
    is the wiring and the best-effort contract, not the tasks insert itself."""

    async def test_result_title_reads_from_each_side(self) -> None:
        bid = "b1"
        assert "победа" in _battle_result_title(bid, Side.A, Winner.A.value)
        assert "поражение" in _battle_result_title(bid, Side.B, Winner.A.value)
        # A real tie (quorum reached on a draw) is "ничья".
        assert "ничья" in _battle_result_title(bid, Side.A, Winner.TIE.value)
        # No quorum is NOT a draw — a failed panel is not evidence of equality.
        no_quorum = _battle_result_title(bid, Side.A, None)
        assert "не определён" in no_quorum
        assert "кворум" in no_quorum
        assert "ничья" not in no_quorum
        assert bid in _battle_result_title(bid, Side.A, Winner.A.value)

    async def test_completed_battle_notifies_both_owners(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.B]
        )
        spy = AsyncMock()
        with patch("app.services.agent_service.AgentService.create_notification_task", spy):
            assert await _settle_in_own_session(session_maker, battle_id, token) is not None

        notified = {call.kwargs["assigned_to_agent_id"] for call in spy.await_args_list}
        assert notified == {agent_a, agent_b}
        for call in spy.await_args_list:
            assert call.kwargs["task_type"] == "battle_result"
            assert call.kwargs["source_key"] == f"battle:{battle_id}:battle_result"
        # A won 2-1: the win/loss framing reaches the right owner.
        by_agent = {
            c.kwargs["assigned_to_agent_id"]: c.kwargs["title"] for c in spy.await_args_list
        }
        assert "победа" in by_agent[agent_a]
        assert "поражение" in by_agent[agent_b]

    async def test_first_recipients_write_survives_a_rollback_of_the_second(
        self, session_maker, db_session, task_id
    ) -> None:
        """Per-recipient isolation is REAL, proven against Postgres.

        The settle-level test below mocks create_notification_task and so can
        only prove both recipients were attempted — not that A's row outlived
        B's failure. This drives _notify_battle_owners directly with a real
        session: the create_notification_task stand-in performs a REAL durable
        write for recipient A (a battle_challenge_cooldowns row on the same
        session, since the battle testcontainer schema has no `tasks` table) and
        RAISES for recipient B. A's row must be found afterwards — committed by
        A's own per-recipient transaction and untouched by B's rollback.
        """
        agent_a = await _new_agent(db_session)
        agent_b = await _new_agent(db_session)
        await db_session.commit()

        battle_id = str(uuid.uuid4())
        attempted: list[str] = []

        async with session_maker() as work_session:

            async def real_write_then_fail(**kwargs):
                aid = kwargs["assigned_to_agent_id"]
                attempted.append(aid)
                if aid == agent_b:
                    raise RuntimeError("notification backend down for recipient two")
                # Recipient A: a genuine INSERT on the SAME session that
                # _notify_battle_owners will commit for this recipient.
                await work_session.execute(
                    text(
                        "INSERT INTO battle_challenge_cooldowns "
                        "(challenger_agent_id, target_agent_id, cooldown_until) "
                        "VALUES (CAST(:a AS UUID), CAST(:b AS UUID), NOW() + INTERVAL '1 hour')"
                    ),
                    {"a": agent_a, "b": agent_b},
                )

            with patch(
                "app.services.agent_service.AgentService.create_notification_task",
                AsyncMock(side_effect=real_write_then_fail),
            ):
                # Must not re-raise even though recipient B blows up.
                await _notify_battle_owners(
                    work_session,
                    battle_id,
                    [
                        (agent_a, "battle_result", "A"),
                        (agent_b, "battle_result", "B"),
                    ],
                )

        assert attempted == [agent_a, agent_b]  # both tried, A first

        # A's write is durable in a FRESH connection — committed by its own
        # transaction and NOT rolled back when B failed.
        async with session_maker() as verify_session:
            row = await verify_session.execute(
                text(
                    "SELECT COUNT(*) FROM battle_challenge_cooldowns "
                    "WHERE challenger_agent_id = CAST(:a AS UUID)"
                ),
                {"a": agent_a},
            )
            assert row.scalar_one() == 1

    async def test_notify_owners_swallows_an_agentservice_construction_failure(
        self, session_maker, db_session, task_id
    ) -> None:
        """The whole body — import + construction included — is inside the guard.

        If AgentService construction fails, _notify_battle_owners must swallow it
        and return; letting it escape would abort the caller's reaper pass AFTER
        a durable terminal transition. (Moving the construction out of the try
        makes this raise.)
        """
        agent_a = await _new_agent(db_session)
        await db_session.commit()

        async with session_maker() as work_session:
            with patch(
                "app.services.agent_service.AgentService",
                side_effect=RuntimeError("AgentService could not be built"),
            ):
                # No exception may cross this call.
                await _notify_battle_owners(
                    work_session, str(uuid.uuid4()), [(agent_a, "battle_result", "A")]
                )

    async def test_notify_failure_on_second_recipient_leaves_the_transition_intact(
        self, session_maker, db_session, task_id
    ) -> None:
        """Integration: settle -> notify both, second fails, verdict stands.

        Honest scope: create_notification_task is MOCKED here (no `tasks` table
        in the battle schema), so this proves the terminal transition survives a
        notify blowup and both recipients are attempted — NOT that A's row
        outlived B's rollback. That durability claim is proven separately, real,
        in test_first_recipients_write_survives_a_rollback_of_the_second.
        """
        battle_id, agent_a, agent_b, token = await _battle_in_judging(
            db_session, task_id, votes=[Vote.A, Vote.A, Vote.A]
        )
        calls: list[str] = []

        async def flaky(**kwargs):
            calls.append(kwargs["assigned_to_agent_id"])
            if len(calls) == 2:
                raise RuntimeError("notification backend down for recipient two")

        with patch(
            "app.services.agent_service.AgentService.create_notification_task",
            AsyncMock(side_effect=flaky),
        ):
            change = await _settle_in_own_session(session_maker, battle_id, token)
        assert change is not None
        assert len(calls) == 2
        assert set(calls) == {agent_a, agent_b}

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
        assert battle["status"] == "completed"
        assert battle["winner"] == "a"
        assert battle["finalized_at"] is not None
        # The Elo change actually landed — the transition is fully durable.
        assert await _elo(session_maker, agent_a) != DEFAULT_ELO

    async def test_expired_challenge_notifies_the_challenger_owner(
        self, session_maker, db_session, task_id
    ) -> None:
        owner_a = await _new_owner(db_session)
        agent_a = await _new_agent(db_session)
        repo = BattleRepository(db_session)
        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
        )  # open challenge, no B
        await db_session.execute(
            text(
                "UPDATE battles SET challenge_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await db_session.commit()

        spy = AsyncMock()
        with patch("app.services.agent_service.AgentService.create_notification_task", spy):
            counts = await reap_once(session_maker)
        assert counts["expired"] >= 1

        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "expired"
        prefix = f"battle:{battle_id}:"
        mine = [c for c in spy.await_args_list if c.kwargs["source_key"].startswith(prefix)]
        assert len(mine) == 1
        assert mine[0].kwargs["assigned_to_agent_id"] == agent_a
        assert mine[0].kwargs["task_type"] == "battle_expired"

    async def test_aborted_battle_notifies_both_owners(
        self, session_maker, db_session, task_id
    ) -> None:
        # A queued battle whose whole claim budget is spent is aborted by the
        # reaper's exhausted-attempts path — a pre-'running' terminal.
        battle_id, agent_a, agent_b = await _build_queued_battle(db_session, task_id)
        async with session_maker() as session:
            # Spend the whole claim budget so the reaper's exhausted-attempts
            # path aborts it. Only the counter matters; the lease stays NULL/NULL
            # (touching lease_expires_at alone would break the token/expiry pair).
            await session.execute(
                text(
                    "UPDATE battles SET lease_attempt_count = :m WHERE id = CAST(:b AS UUID)"
                ),
                {"m": POLL_MAX_ATTEMPTS, "b": battle_id},
            )
            await session.commit()

        spy = AsyncMock()
        with patch("app.services.agent_service.AgentService.create_notification_task", spy):
            counts = await reap_once(session_maker)
        assert counts["aborted"] >= 1

        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "aborted"
        notified = {
            c.kwargs["assigned_to_agent_id"]
            for c in spy.await_args_list
            if c.kwargs["source_key"] == f"battle:{battle_id}:battle_aborted"
        }
        assert notified == {agent_a, agent_b}


class TestLifecycleBranches:
    """FIX 3: the four untested lifecycle branches, each asserting the terminal
    state AND that no fighter is left pinned by a reservation."""

    async def test_deadline_timeout_synthesizes_a_silent_final_and_completes(
        self, session_maker, db_session, task_id
    ) -> None:
        # Running, side A answered, side B silent; the wall clock runs out.
        async with session_maker() as session:
            battle_id, agent_a, agent_b, _token = await _battle_running(session, task_id)
            repo = BattleRepository(session)
            assert await repo.add_submission(battle_id, Side.A, 1, "A answer", is_final=True)
            # Age every wall clock consistently so the timeline stays legal
            # (challenged < queued < started < deadline) while the deadline sits
            # in the past; free the row so the reconciler's running phase claims it.
            await session.execute(
                text(
                    "UPDATE battles SET challenged_at = NOW() - INTERVAL '30 minutes', "
                    "queued_at = NOW() - INTERVAL '20 minutes', "
                    "started_at = NOW() - INTERVAL '10 minutes', "
                    "deadline_at = NOW() - INTERVAL '1 second', "
                    "lease_token = NULL, lease_expires_at = NULL WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        with _no_transport(), patch(
            "app.services.battle_runner.BattleRunner._run_one_half", side_effect=_fake_half
        ), patch("app.services.agent_service.AgentService.create_notification_task", AsyncMock()):
            await reconcile_once(
                session_factory=session_maker, gate=None,
                provider={"api_key": "k", "base_url": "http://u"},
            )

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
            submissions = await BattleRepository(session).list_submissions(battle_id)
        assert battle["status"] == "completed"
        assert battle["winner"] == "a"
        b_final = [s for s in submissions if s["side"] == "b" and s["is_final"]]
        assert len(b_final) == 1
        assert b_final[0]["truncated"] is True
        assert b_final[0]["seq_no"] == SILENT_FIGHTER_SEQ_NO
        # No fighter left pinned by a reservation once the battle completes.
        assert await _reservation_count(session_maker, battle_id) == 0

    async def test_owner_decline_writes_cooldown_and_leaves_no_reservations(
        self, session_maker, db_session, task_id
    ) -> None:
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a = await _new_eligible_agent(db_session, owner_a)
        agent_b = await _new_eligible_agent(db_session, owner_b)
        repo = BattleRepository(db_session)
        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
            agent_b_id=agent_b,
            agent_b_owner_snapshot=owner_b,
        )
        await db_session.commit()

        async with session_maker() as session:
            svc = BattleService(session)
            declined = await svc.decline(battle_id, owner_b)
            await session.commit()
        assert declined is not None
        assert declined["status"] == "declined"

        async with session_maker() as session:
            battle = await BattleRepository(session).get(battle_id)
            cooldown = await session.execute(
                text(
                    "SELECT cooldown_until > NOW() FROM battle_challenge_cooldowns "
                    "WHERE challenger_agent_id = CAST(:a AS UUID) "
                    "AND target_agent_id = CAST(:b AS UUID)"
                ),
                {"a": agent_a, "b": agent_b},
            )
        assert battle["status"] == "declined"
        assert cooldown.scalar_one() is True  # a live 24h cooldown was stamped
        assert await _reservation_count(session_maker, battle_id) == 0

    async def test_open_challenge_is_claimed_then_accepted(
        self, session_maker, db_session, task_id
    ) -> None:
        owner_a, owner_b = await _new_owner(db_session), await _new_owner(db_session)
        agent_a = await _new_eligible_agent(db_session, owner_a)
        agent_b = await _new_eligible_agent(db_session, owner_b)
        repo = BattleRepository(db_session)
        battle_id = await repo._create_battle(
            task_id=task_id,
            agent_a_id=agent_a,
            agent_a_owner_snapshot=owner_a,
            challenge_ttl_seconds=3600,
        )  # open: no target
        await db_session.commit()

        async with session_maker() as session:
            claimed = await BattleRepository(session).claim_open_challenge_as_owner(
                battle_id=battle_id,
                agent_b_id=agent_b,
                claiming_user_id=owner_b,
                target_cap=100,
                target_window_seconds=3600,
            )
            await session.commit()
        assert claimed is not None
        # Claiming is not consent: still pending, but the slot is filled.
        assert claimed["status"] == "challenge_pending"
        assert str(claimed["agent_b_id"]) == agent_b

        async with session_maker() as session:
            accepted = await BattleRepository(session).accept_as_owner(battle_id, owner_b)
            await session.commit()
        assert accepted is not None
        assert accepted["status"] == "accepted"

    async def test_no_ack_reservation_lapses_and_the_reaper_expires_the_battle(
        self, session_maker, db_session, task_id
    ) -> None:
        # A battle that reserved both fighters and armed ready-checks, but which
        # nobody ever ACKed: the reservations and readiness lease lapse, the
        # challenge deadline passes, and the reaper routes it to 'expired'.
        battle_id, agent_a, agent_b = await _build_queued_battle(db_session, task_id)
        # Undo the queue admission so the battle sits in 'reserved' (the no-ACK
        # shape) and force every wall clock into the past.
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET status = 'reserved', queued_at = NULL, "
                    "challenge_expires_at = NOW() - INTERVAL '1 second', "
                    "ready_lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            # The unacked ready-checks lapse by TTL (no future-check on
            # agent_events). The reservations stay future-valid — mark_expired
            # releases them on the terminal transition regardless of their TTL.
            await session.execute(
                text(
                    "UPDATE agent_events SET expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE target_agent_id IN (CAST(:a AS UUID), CAST(:b AS UUID))"
                ),
                {"a": agent_a, "b": agent_b},
            )
            await session.commit()

        spy = AsyncMock()
        with patch("app.services.agent_service.AgentService.create_notification_task", spy):
            counts = await reap_once(session_maker)
        assert counts["expired"] >= 1

        async with session_maker() as session:
            assert (await BattleRepository(session).get(battle_id))["status"] == "expired"
        # No pinned fighter, no live obligation, and the challenger's owner was told.
        assert await _reservation_count(session_maker, battle_id) == 0
        assert await _live_event_count(session_maker, (agent_a, agent_b)) == 0
        expired_notif = [
            c for c in spy.await_args_list
            if c.kwargs["source_key"] == f"battle:{battle_id}:battle_expired"
        ]
        assert len(expired_notif) == 1
        assert expired_notif[0].kwargs["assigned_to_agent_id"] == agent_a
