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

from app.core.rating import DEFAULT_ELO, K_FACTOR
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import Side, TaskSource, Vote
from app.services.battle_judges import JUDGE_KIND_LLM, JUDGE_MODEL, replicate_seed
from app.services.battle_runner import BattleRunner, reconcile_once
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
            api_key="unused", base_url="http://unused",
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
