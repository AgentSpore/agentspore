"""Tests for phase-1 step 7 — battle schema and atomic state machine (V66).

The invariant under test, stated so it can be falsified:

    Legal transitions are atomic, and terminal battles cannot restart.

Concretely: every transition is one compare-and-set naming its expected old
state, so at most ONE concurrent caller can move a battle out of a given
state, no terminal row can move at all, and reserving both fighters inserts
two rows or none.

These run the REAL V66 migration file against testcontainers Postgres, never
a re-declared inline schema — a typo or a missing CHECK in the migration must
fail these tests, which is the entire point of testing a migration. V65 is
applied too, because battles.ready_check_event_id_a/b are FKs into
agent_events: readiness is bound to exact event ids, and the database is what
enforces those ids exist.

Every test here needs Docker (@pytest.mark.integration). A mock cannot prove
atomicity: the race is arbitrated by real row locks in a real transaction, so
mocking it would only prove that a mock returns what it was told to.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository, ReservationConflictError
from app.schemas.battles import BattleStatus, Side, TaskSource, TaskStatus

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
V65_PATH = MIGRATIONS / "V65__agent_events.sql"
V66_PATH = MIGRATIONS / "V66__battles.sql"
V67_PATH = MIGRATIONS / "V67__battle_task_secrecy.sql"

# Minimal FK targets only. Every battle table's DDL comes from the real V66.
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
    created_at TIMESTAMPTZ DEFAULT now()
);
"""

RUBRIC = [{"criterion": "correctness", "weight": 1.0}]

# Every test needs Docker, and all of them share the module-scoped engine,
# so they must also share its event loop.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    """Engine with the FK targets + the REAL V65 and V66 migrations applied.

    Module-scoped because a versioned migration runs exactly once — that is
    what Flyway does, and re-applying V66 per test would demand an idempotency
    the real migration neither has nor needs. Tests stay independent by using
    fresh UUIDs rather than a fresh schema.
    """
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()};{V67_PATH.read_text()}"
    # One transaction for the whole migration, mirroring Flyway's V__ handling.
    # Statements are applied one at a time because asyncpg refuses multiple
    # commands in a single prepared statement.
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
async def db_session(session_maker):
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def owner_id(db_session) -> str:
    uid = str(uuid.uuid4())
    await db_session.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"owner-{uid[:8]}@example.test"},
    )
    await db_session.commit()
    return uid


@pytest_asyncio.fixture(loop_scope="module")
async def agent_ids(db_session) -> tuple[str, str, str]:
    """Three fighters: A and B duel, C is the bystander for the cross-role race."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for aid in ids:
        await db_session.execute(
            text(
                "INSERT INTO agents (id, handle, name) "
                "VALUES (CAST(:id AS UUID), :h, :n)"
            ),
            {"id": aid, "h": f"fighter-{aid[:8]}", "n": "Fighter"},
        )
    await db_session.commit()
    return ids[0], ids[1], ids[2]


@pytest_asyncio.fixture(loop_scope="module")
async def task_id(db_session, owner_id) -> str:
    repo = BattleRepository(db_session)
    tid = await repo.create_task(
        source=TaskSource.GENERATED,
        category="general",
        title="Write a parser",
        prompt="Parse this log format.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        created_by_user_id=owner_id,
    )
    await db_session.commit()
    return tid


@pytest_asyncio.fixture(loop_scope="module")
async def accepted_battle(db_session, task_id, agent_ids, owner_id) -> str:
    """A battle whose owner consented — the state just before reservation."""
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_id,
    )
    assert await repo._mark_accepted(battle_id) is not None
    await db_session.commit()
    return battle_id


@pytest_asyncio.fixture(loop_scope="module")
async def reserved_battle(db_session, accepted_battle, agent_ids) -> tuple[str, int]:
    """A reserved battle with a live readiness generation. Returns (id, gen)."""
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    events = AgentEventRepository(db_session)

    won = await repo.reserve_both(accepted_battle, agent_a, agent_b, 60)
    assert len(won) == 2

    # Domain TTL, never the 32400s default: a ready check that stays ACK-able
    # for nine hours is not a readiness check.
    event_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    event_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(accepted_battle, event_a, event_b, 60)
    assert row is not None
    await db_session.commit()
    return accepted_battle, row["readiness_generation"]


async def queue_in_own_session(session_maker, battle_id: str, generation: int):
    """Attempt reserved -> queued in an independent transaction.

    Each caller gets its own session, hence its own connection: the race has
    to be arbitrated by real Postgres row locks, not by two coroutines sharing
    one transaction (which would prove nothing).
    """
    async with session_maker() as session:
        repo = BattleRepository(session)
        row = await repo._mark_queued(battle_id, generation)
        await session.commit()
        return row


async def test_battle_state_machine_transition_is_single_winner_and_terminal_is_immutable(
    session_maker, db_session, reserved_battle
):
    """THE invariant: one winner per transition, and terminal means terminal."""
    battle_id, generation = reserved_battle

    # --- one winner -------------------------------------------------------
    # Two workers race the same reserved -> queued transition against the same
    # old state. Exactly one may win; the loser must be told it lost.
    results = await asyncio.gather(
        queue_in_own_session(session_maker, battle_id, generation),
        queue_in_own_session(session_maker, battle_id, generation),
    )
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"
    assert winners[0]["status"] == BattleStatus.QUEUED.value
    assert winners[0]["queued_at"] is not None

    # A third attempt after the fact is not a winner either: the CAS compares
    # against 'reserved', which no longer holds.
    assert await queue_in_own_session(session_maker, battle_id, generation) is None

    # --- drive to terminal ------------------------------------------------
    repo = BattleRepository(db_session)
    token = str(uuid.uuid4())
    running = await repo._mark_running(battle_id, token, 30)
    assert running is not None
    # deadline_at is derived in the database from the frozen snapshot, so both
    # sides share one wall clock regardless of any worker's own clock.
    assert running["deadline_at"] > running["started_at"]

    # Judging is illegal before the deadline unless both sides submitted final.
    assert await repo.mark_judging(battle_id, token) is None
    assert await repo.add_submission(battle_id, Side.A, 0, "a-answer", is_final=True)
    assert await repo.add_submission(battle_id, Side.B, 0, "b-answer", is_final=True)
    # A worker without the claim token may not move it either.
    assert await repo.mark_judging(battle_id, str(uuid.uuid4())) is None
    assert await repo.mark_judging(battle_id, token) is not None
    assert await repo.finalize(battle_id, str(uuid.uuid4()), "b", "impostor") is None
    completed = await repo.finalize(battle_id, token, "a", "majority: a=3")
    assert completed is not None
    assert completed["status"] == BattleStatus.COMPLETED.value
    assert completed["finalized_at"] is not None
    await db_session.commit()

    # --- terminal is immutable -------------------------------------------
    # Every transition out of a completed battle must return zero rows. A
    # second finalizer must not be able to apply Elo twice.
    assert await repo.finalize(battle_id, token, "b", "second finalizer") is None
    assert await repo._mark_accepted(battle_id) is None
    assert await repo._mark_declined(battle_id) is None
    assert await repo._mark_queued(battle_id, generation) is None
    assert await repo._mark_running(battle_id, str(uuid.uuid4()), 30) is None
    assert await repo.mark_judging(battle_id, token) is None
    assert await repo.mark_expired(battle_id) is None
    assert await repo.mark_aborted(battle_id, "too late") is None
    assert await repo.release_readiness(battle_id) is None
    await db_session.commit()

    final = await repo.get(battle_id)
    assert final["status"] == BattleStatus.COMPLETED.value
    assert final["winner"] == "a", "a losing finalizer must not overwrite the verdict"


async def test_partial_reservation_cannot_be_committed(
    db_session, task_id, agent_ids, owner_id, reserved_battle
):
    """One agent, one active battle — enforced, not requested.

    This is the case two partial unique indexes on agent_a_id/agent_b_id would
    miss entirely: agent A is already side A in battle 1, and battle 2 tries to
    take them as side B. Different column, different index, no conflict — but
    the same LLM key, spent twice.

    The test deliberately does NOT roll back on the caller's behalf. It COMMITS
    after the failed reservation, because a test that rolls back only proves
    the caller was disciplined. The step-8 caller does not exist yet, so the
    property under test is that a partial reservation is physically
    uncommittable — not that a future author remembers to unwind it.
    """
    agent_a, _, agent_c = agent_ids
    repo = BattleRepository(db_session)

    second = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_c,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_a,
        agent_b_owner_snapshot=owner_id,
    )
    await db_session.commit()

    # agent_a is already reserved by the first battle; agent_c is free.
    with pytest.raises(ReservationConflictError):
        await repo.reserve_both(second, agent_c, agent_a, 60)

    # The caller ignores the failure and commits anyway — the bad caller.
    await db_session.commit()

    held = await db_session.execute(
        text(
            "SELECT battle_id FROM battle_reservations "
            "WHERE agent_id = CAST(:id AS UUID)"
        ),
        {"id": agent_c},
    )
    assert held.first() is None, "a partial reservation survived a commit"


async def test_lapsed_reservation_is_reclaimed_without_waiting_for_a_reaper(
    db_session, task_id, agent_ids, owner_id, reserved_battle
):
    """An expired reservation must not hold a fighter hostage."""
    agent_a, _, agent_c = agent_ids
    repo = BattleRepository(db_session)
    await db_session.execute(
        text(
            "UPDATE battle_reservations "
            "SET created_at = NOW() - INTERVAL '10 seconds', "
            "    reserved_until = NOW() - INTERVAL '1 second' "
            "WHERE agent_id = CAST(:id AS UUID)"
        ),
        {"id": agent_a},
    )
    second = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_c,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_a,
        agent_b_owner_snapshot=owner_id,
    )
    won = await repo.reserve_both(second, agent_c, agent_a, 60)
    assert sorted(won) == sorted([agent_c, agent_a])
    await db_session.commit()


async def test_stale_readiness_generation_never_queues(
    db_session, reserved_battle, agent_ids
):
    """A re-armed battle ignores evidence from the previous attempt.

    The ready lease lapsing and being re-armed is the ordinary path, not an
    edge case. An ACK that belongs to the abandoned generation must never
    queue the battle: that is exactly how a stale event would drag an absent
    fighter into a battle they never confirmed.
    """
    battle_id, old_generation = reserved_battle
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    events = AgentEventRepository(db_session)

    # A live lease may not be released: only its lapse authorises a re-arm.
    assert await repo.release_readiness(battle_id) is None
    await db_session.execute(
        text(
            "UPDATE battles SET ready_lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE id = CAST(:bid AS UUID)"
        ),
        {"bid": battle_id},
    )
    assert await repo.release_readiness(battle_id) is not None
    event_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    event_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    rearmed = await repo.arm_readiness(battle_id, event_a, event_b, 60)
    assert rearmed is not None
    new_generation = rearmed["readiness_generation"]
    assert new_generation > old_generation

    assert await repo._mark_queued(battle_id, old_generation) is None
    assert await repo._mark_queued(battle_id, new_generation) is not None


async def test_open_challenge_claim_has_exactly_one_winner(
    db_session, session_maker, task_id, agent_ids, owner_id
):
    """Two candidates race for the empty B slot of an open challenge."""
    agent_a, agent_b, agent_c = agent_ids
    repo = BattleRepository(db_session)
    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
    )
    await db_session.commit()

    opened = await repo.get(battle_id)
    assert opened["agent_b_id"] is None, "an open challenge starts with no opponent"

    async def claim(candidate: str):
        async with session_maker() as session:
            row = await BattleRepository(session)._claim_open_challenge(
                battle_id, candidate, owner_id
            )
            await session.commit()
            return row

    results = await asyncio.gather(claim(agent_b), claim(agent_c))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    # Claiming is not consent: the battle waits for B's owner to accept.
    assert winners[0]["status"] == BattleStatus.CHALLENGE_PENDING.value
    assert winners[0]["agent_b_accepted_at"] is None


async def test_only_one_final_submission_per_side(db_session, reserved_battle):
    """A late final answer cannot displace the one already recorded.

    The truncated synthetic submission a reconciler writes for a silent
    fighter takes this same slot, which is what stops a straggler from
    overwriting the record of their own timeout.
    """
    battle_id, generation = reserved_battle
    repo = BattleRepository(db_session)

    # add_submission now requires a 'running' battle before its deadline (review
    # fix F6: the router's status/deadline checks are TOCTOU, so the guard moved
    # into the INSERT). Drive the reserved battle to running before exercising the
    # submission-table constraints this test is about.
    assert await repo._mark_queued(battle_id, generation) is not None
    assert await repo._mark_running(battle_id, str(uuid.uuid4()), 30) is not None

    assert await repo.add_submission(battle_id, Side.A, 0, "checkpoint", is_final=False)
    assert await repo.add_submission(battle_id, Side.A, 1, "answer", is_final=True)
    # Same seq_no: primary key collision.
    assert not await repo.add_submission(battle_id, Side.A, 1, "dup", is_final=False)
    # Different seq_no, but a second final for the same side.
    assert not await repo.add_submission(battle_id, Side.A, 2, "late", is_final=True)
    await db_session.commit()

    rows = await repo.list_submissions(battle_id)
    assert [r["seq_no"] for r in rows] == [0, 1]
    assert sum(1 for r in rows if r["is_final"]) == 1


async def test_replicate_pair_stores_both_halves_but_collapses_to_one_vote(
    db_session, reserved_battle
):
    """The ab/ba pair is two raw runs and exactly one collapsed vote.

    Both halves of a replicate must be storable — the raw key includes
    presented_order — while the collapsed key without it caps three paired
    replicates at three votes, so a pair can never be counted as two.
    """
    battle_id, _ = reserved_battle
    repo = BattleRepository(db_session)

    ab = await repo.create_judge_run(battle_id, "llm", "glm-4.5-flash", "seed1", "ab")
    ba = await repo.create_judge_run(battle_id, "llm", "glm-4.5-flash", "seed1", "ba")
    assert ab is not None and ba is not None, "a replicate is two runs, not one"
    # The same half twice is the same slot.
    assert await repo.create_judge_run(battle_id, "llm", "glm-4.5-flash", "seed1", "ab") is None

    first = await repo.upsert_judgement(
        battle_id, "llm", "glm-4.5-flash", "seed1", "a", confidence=0.8
    )
    assert first is not None
    second = await repo.upsert_judgement(
        battle_id, "llm", "glm-4.5-flash", "seed1", "b", confidence=0.9
    )
    assert second is None, "one replicate may not cast two collapsed votes"
    await db_session.commit()

    assert len(await repo.list_judge_runs(battle_id)) == 2
    assert len(await repo.list_judgements(battle_id)) == 1


async def test_stale_worker_cannot_write_a_judge_run_it_no_longer_owns(
    db_session, reserved_battle
):
    """A former owner's answer is discarded, not merged.

    The scheduler lease cannot prevent this: losing leadership does not stop
    an in-flight run_once(), so the old worker really does arrive with a real
    verdict. The row token is what rejects it.
    """
    battle_id, _ = reserved_battle
    repo = BattleRepository(db_session)
    run_id = await repo.create_judge_run(
        battle_id, "llm", "glm-4.5-flash", "seed1", "ab"
    )
    stale_token = str(uuid.uuid4())
    assert await repo.claim_judge_run(run_id, stale_token, 30) is not None

    # A second worker cannot steal a live lease.
    fresh_token = str(uuid.uuid4())
    assert await repo.claim_judge_run(run_id, fresh_token, 30) is None

    # The rightful owner writes; an impostor's identical write returns nothing.
    assert await repo.complete_judge_run(run_id, str(uuid.uuid4()), "b") is None
    done = await repo.complete_judge_run(run_id, stale_token, "a", confidence=0.7)
    assert done is not None and done["vote"] == "a"
    # And the write is not repeatable — status is no longer 'running'.
    assert await repo.complete_judge_run(run_id, stale_token, "b") is None
    await db_session.commit()


@pytest.mark.parametrize(
    "column,value",
    [
        # A broken judge must never be able to mint tie-Elo, and a confidence
        # outside [0,1] must not be storable at all.
        ("vote", "'maybe'"),
        ("confidence", "1.5"),
        ("confidence", "-0.1"),
        # Fits VARCHAR(3), so the CHECK is what rejects it, not the length.
        ("presented_order", "'aba'"),
        ("judge_kind", "'oracle'"),
    ],
)
async def test_judge_run_rejects_illegal_values(db_session, reserved_battle, column, value):
    """CHECK constraints, not application code, make these unrepresentable."""
    battle_id, _ = reserved_battle
    columns = {
        "battle_id": f"CAST('{battle_id}' AS UUID)",
        "judge_kind": "'llm'",
        "judge_ref": "'glm-4.5-flash'",
        "replicate_seed": "'seed1'",
        "presented_order": "'ab'",
        "vote": "'a'",
        "confidence": "0.5",
    }
    columns[column] = value
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                f"INSERT INTO battle_judge_runs ({', '.join(columns)}) "
                f"VALUES ({', '.join(columns.values())})"
            )
        )
    await db_session.rollback()


async def test_abstain_is_storable_and_distinct_from_tie(db_session, reserved_battle):
    """Malformed judge output has an honest place to land.

    Without 'abstain' in the enum, a broken judge's output would have to be
    mapped onto 'tie' — which moves both fighters' Elo on the strength of a
    parse failure.
    """
    battle_id, _ = reserved_battle
    repo = BattleRepository(db_session)
    for seed, vote in (("seed1", "abstain"), ("seed2", "error"), ("seed3", "tie")):
        assert (
            await repo.upsert_judgement(battle_id, "llm", "glm-4.5-flash", seed, vote)
            is not None
        )
    await db_session.commit()
    votes = {j["vote"] for j in await repo.list_judgements(battle_id)}
    assert votes == {"abstain", "error", "tie"}


async def test_position_sensitive_flag_only_describes_a_tie(db_session, reserved_battle):
    """position_sensitive marks an A/B split that collapsed to a tie."""
    battle_id, _ = reserved_battle
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                """
                INSERT INTO battle_judgements
                    (battle_id, judge_kind, judge_ref, replicate_seed, vote,
                     position_sensitive)
                VALUES (CAST(:bid AS UUID), 'llm', 'glm-4.5-flash', 's1', 'a', TRUE)
                """
            ),
            {"bid": battle_id},
        )
    await db_session.rollback()


async def test_a_winner_requires_a_completed_battle(db_session, reserved_battle):
    """A verdict cannot exist on a battle that has not been judged."""
    battle_id, _ = reserved_battle
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE battles SET winner = 'a' WHERE id = CAST(:bid AS UUID)"
            ),
            {"bid": battle_id},
        )
    await db_session.rollback()


async def test_battle_task_rubric_must_be_a_list_of_criteria(db_session, owner_id):
    """A non-array rubric would silently produce vibes-based judging."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                """
                INSERT INTO battle_tasks (source, title, prompt, rubric)
                VALUES ('generated', 'T', 'P', CAST('{"criterion": "x"}' AS JSONB))
                """
            )
        )
    await db_session.rollback()


async def test_reservation_must_expire_in_the_future(db_session, reserved_battle, agent_ids):
    """A reservation that is already expired at insert is not a reservation."""
    battle_id, _ = reserved_battle
    _, _, agent_c = agent_ids
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                """
                INSERT INTO battle_reservations (agent_id, battle_id, reserved_until)
                VALUES (CAST(:aid AS UUID), CAST(:bid AS UUID), NOW() - INTERVAL '1 hour')
                """
            ),
            {"aid": agent_c, "bid": battle_id},
        )
    await db_session.rollback()


async def test_deleting_a_fighter_mid_battle_is_blocked(db_session, reserved_battle, agent_ids):
    """ON DELETE RESTRICT: a battle keeps referring to real fighters.

    The alternative — cascade or NULL the agent away — would silently rewrite
    the history of a battle that already spent someone's inference.
    """
    agent_a, _, _ = agent_ids
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM agents WHERE id = CAST(:id AS UUID)"), {"id": agent_a}
        )
    await db_session.rollback()


async def test_lapsed_judge_run_is_reclaimable_and_dead_holder_write_bounces(
    db_session, reserved_battle
):
    """A worker that died mid-call must not strand its replicate forever.

    idx_battle_judge_runs_lease exists to find exactly these rows: a 'running'
    row whose lease lapsed. Refusing to reclaim them would leave the index with
    no possible consumer — and the replicate permanently stuck.

    The reclaim rotates the token, which is what makes the dead holder's late
    answer bounce if it ever wakes up.
    """
    battle_id, _ = reserved_battle
    repo = BattleRepository(db_session)
    run_id = await repo.create_judge_run(battle_id, "llm", "glm-4.5-flash", "s1", "ab")

    dead_token = str(uuid.uuid4())
    assert await repo.claim_judge_run(run_id, dead_token, 30) is not None
    # A live lease is never stolen.
    assert await repo.claim_judge_run(run_id, str(uuid.uuid4()), 30) is None

    await db_session.execute(
        text(
            "UPDATE battle_judge_runs SET lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE id = CAST(:rid AS UUID)"
        ),
        {"rid": run_id},
    )
    new_token = str(uuid.uuid4())
    assert await repo.claim_judge_run(run_id, new_token, 30) is not None

    # The dead worker wakes up holding a real verdict — and is refused.
    assert await repo.complete_judge_run(run_id, dead_token, "b") is None
    assert await repo.complete_judge_run(run_id, new_token, "a") is not None
    await db_session.commit()


async def test_expired_lease_holder_cannot_publish_a_judge_verdict(
    db_session, reserved_battle
):
    """Token match alone is not ownership — the lease must still be live."""
    battle_id, _ = reserved_battle
    repo = BattleRepository(db_session)
    run_id = await repo.create_judge_run(battle_id, "llm", "glm-4.5-flash", "s2", "ba")
    token = str(uuid.uuid4())
    assert await repo.claim_judge_run(run_id, token, 30) is not None

    await db_session.execute(
        text(
            "UPDATE battle_judge_runs SET lease_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE id = CAST(:rid AS UUID)"
        ),
        {"rid": run_id},
    )
    assert await repo.complete_judge_run(run_id, token, "a") is None, (
        "a lapsed holder published a stale verdict"
    )
    await db_session.rollback()


async def test_judge_run_retries_are_bounded(db_session, reserved_battle):
    """A handler that fails every time must not be re-claimed forever."""
    battle_id, _ = reserved_battle
    repo = BattleRepository(db_session)
    run_id = await repo.create_judge_run(battle_id, "llm", "glm-4.5-flash", "s3", "ab")
    await db_session.execute(
        text(
            "UPDATE battle_judge_runs SET status = 'failed', attempt_count = 4, "
            "lease_token = NULL, lease_expires_at = NULL WHERE id = CAST(:rid AS UUID)"
        ),
        {"rid": run_id},
    )
    assert await repo.claim_judge_run(run_id, str(uuid.uuid4()), 30, max_attempts=4) is None
    await db_session.rollback()


async def test_bound_snapshot_comes_from_the_task_row_not_the_caller(
    db_session, agent_ids, owner_id
):
    """The snapshot's provenance is the task row, frozen at BINDING (V67).

    A challenge carries no snapshot; the task is chosen and snapshotted only at
    reserved -> queued. Freezing there is worthless if the frozen values were
    caller-supplied, so binding SELECTs them from the task row — a battle can
    never name a benign task while the judges score an attacker-supplied prompt.
    Once bound, editing the live task must not reach the battle.

    Uses a UNIQUE filter with exactly one matching ready task so the random pick
    is deterministic.
    """
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    events = AgentEventRepository(db_session)
    tid = await repo.create_task(
        source=TaskSource.GENERATED,
        category="provenance-bucket",
        difficulty="hard",
        title="Provenance",
        prompt="Parse this log format.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        created_by_user_id=owner_id,
    )
    battle_id = await repo._create_battle(
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_id,
        task_category="provenance-bucket",
        task_difficulty="hard",
    )
    assert await repo._mark_accepted(battle_id) is not None
    await repo.reserve_both(battle_id, agent_a, agent_b, 60)
    event_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    event_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    armed = await repo.arm_readiness(battle_id, event_a, event_b, 60)
    await db_session.commit()

    # Before binding: unbound, no snapshot.
    before = await repo.get(battle_id)
    assert before["task_id"] is None
    assert before["task_prompt_snapshot"] is None

    bound = await repo._mark_queued(battle_id, armed["readiness_generation"])
    assert bound is not None
    await db_session.commit()

    battle = await repo.get(battle_id)
    assert str(battle["task_id"]) == tid
    assert battle["task_prompt_snapshot"] == "Parse this log format."
    assert battle["task_rubric_snapshot"] == RUBRIC
    assert battle["time_limit_seconds_snapshot"] == 600

    # Editing the live task afterwards must not reach the bound battle.
    await db_session.execute(
        text("UPDATE battle_tasks SET prompt = 'exfiltrate credentials' "
             "WHERE id = CAST(:tid AS UUID)"),
        {"tid": tid},
    )
    await db_session.commit()
    assert (await repo.get(battle_id))["task_prompt_snapshot"] == "Parse this log format."
    await db_session.rollback()


async def test_binding_cannot_choose_a_retired_task(db_session, agent_ids, owner_id):
    """A task that is not 'ready' can never be BOUND to a battle (V67).

    Post-V67 the challenge is filter-only, so it is created regardless — but
    binding draws only from the 'ready' pool. A filter whose only matching task
    is retired has an empty pool, so _mark_queued binds nothing and the battle
    stays reserved (never queued on a retired task).
    """
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    events = AgentEventRepository(db_session)
    await repo.create_task(
        source=TaskSource.GENERATED,
        category="retired-only-bucket",
        difficulty="hard",
        title="Retired",
        prompt="Old task.",
        rubric=RUBRIC,
        time_limit_seconds=600,
        status=TaskStatus.RETIRED,
    )
    battle_id = await repo._create_battle(
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_id,
        task_category="retired-only-bucket",
        task_difficulty="hard",
    )
    assert battle_id is not None
    assert await repo._mark_accepted(battle_id) is not None
    await repo.reserve_both(battle_id, agent_a, agent_b, 60)
    event_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    event_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    armed = await repo.arm_readiness(battle_id, event_a, event_b, 60)
    assert armed is not None
    # No ready task matches the filter -> binding chooses nothing.
    assert await repo._mark_queued(battle_id, armed["readiness_generation"]) is None
    await db_session.rollback()


@pytest.mark.parametrize(
    "status,note",
    [
        ("judging", "judging with no consent, queue, start or deadline"),
        ("running", "running with no start or deadline"),
        ("completed", "completed with no start"),
    ],
)
async def test_direct_insert_cannot_forge_a_started_battle(
    db_session, task_id, agent_ids, owner_id, status, note
):
    """CHECK constraints, not repository discipline, make this unrepresentable.

    battle_repo hardcodes 'challenge_pending' and never takes status as a
    parameter, so this is unreachable through today's code — but "unreachable
    through the code we happened to write" is not an invariant, and the
    terminal states were already bound to their timestamps.
    """
    agent_a, agent_b, _ = agent_ids
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                """
                INSERT INTO battles
                    (task_id, agent_a_id, agent_b_id, agent_a_owner_snapshot,
                     challenge_expires_at, task_prompt_snapshot,
                     task_rubric_snapshot, time_limit_seconds_snapshot, status)
                VALUES
                    (CAST(:tid AS UUID), CAST(:a AS UUID), CAST(:b AS UUID),
                     CAST(:o AS UUID), NOW() + INTERVAL '1 hour', 'P',
                     CAST('[]' AS JSONB), 600, :status)
                """
            ),
            {"tid": task_id, "a": agent_a, "b": agent_b, "o": owner_id, "status": status},
        )
    await db_session.rollback()


async def test_reserved_battle_must_have_an_armed_readiness_generation(
    db_session, accepted_battle
):
    """A 'reserved' battle with no armed ready-check is not reserved at all."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE battles SET status = 'reserved' WHERE id = CAST(:bid AS UUID)"),
            {"bid": accepted_battle},
        )
    await db_session.rollback()


async def test_consent_is_required_past_acceptance(db_session, task_id, agent_ids, owner_id):
    """No state past 'accepted' may exist without the owner's consent."""
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_id,
    )
    await db_session.commit()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE battles SET status = 'accepted' WHERE id = CAST(:bid AS UUID)"),
            {"bid": battle_id},
        )
    await db_session.rollback()


async def test_an_expired_challenge_cannot_be_armed_or_left_hanging(
    db_session, task_id, agent_ids, owner_id
):
    """Consent a second before the challenge lapsed does not arm it hours later.

    And 'accepted' has a way out: without an expiry path it would hang forever,
    since mark_expired used to know only about challenge_pending.
    """
    agent_a, agent_b, _ = agent_ids
    repo = BattleRepository(db_session)
    events = AgentEventRepository(db_session)
    battle_id = await repo._create_battle(
        task_id=task_id,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_id,
        challenge_ttl_seconds=3600,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_id,
    )
    assert await repo._mark_accepted(battle_id) is not None
    await db_session.execute(
        text(
            "UPDATE battles SET challenge_expires_at = NOW() - INTERVAL '1 second' "
            "WHERE id = CAST(:bid AS UUID)"
        ),
        {"bid": battle_id},
    )
    event_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    event_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    assert await repo.arm_readiness(battle_id, event_a, event_b, 60) is None

    expired = await repo.mark_expired(battle_id)
    assert expired is not None and expired["status"] == BattleStatus.EXPIRED.value
    await db_session.commit()
