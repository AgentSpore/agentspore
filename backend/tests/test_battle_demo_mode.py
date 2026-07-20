"""Tests for demo battle mode (V71) — an UNRATED battle a user's agent fights
against the platform demo opponent, with ZERO human action on the demo side.

Integration by necessity, exactly like test_battle_runner.py: the properties
under test are arbitrated by real SQL (the rated gate's CAS, the both-sides-acked
predicate, the partial unique index on submissions) against the real V66–V71
migrations on testcontainers Postgres. The judge model and the demo opponent's
answer call are mocked — both go through battle_judges.call_judge_model, so ONE
patch covers the whole panel AND the demo answer.

What each test falsifies is stated on it. The rating-suppression test names its
mutation: delete the ``demo`` early-return in _decide_rated_eligibility and it
goes red (a cross-owner demo battle would then rate).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.rating import DEFAULT_ELO
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import Side, TaskSource, Vote
from app.services.battle_judges import JUDGE_KIND_LLM, JUDGE_MODEL, replicate_seed
from app.services.battle_runner import BattleRunner, reconcile_once
from app.services.battle_service import BattleService
from app.services.connection_manager import DeliveryResult

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
_MIG_FILES = [
    "V65__agent_events.sql",
    "V66__battles.sql",
    "V67__battle_task_secrecy.sql",
    "V68__battle_anti_abuse.sql",
    "V69__battle_injection_stop_reason.sql",
    "V70__battle_user_tasks.sql",
    "V71__battle_demo_mode.sql",
]

RUBRIC = [{"key": "correctness", "description": "Does it work?", "weight": 1.0}]

# The judge/demo-answer mock: a valid judge reply. As the demo opponent's answer
# it is simply stored as its submission content; as a judge reply it parses to a
# vote for the first-presented submission.
VALID_JUDGE_REPLY = (
    '{"vote": "submission_alpha", "confidence": 0.9, "reasoning": "ok", '
    '"scores": {"correctness": 1.0}}'
)

# users carries is_admin (the V71 seed reads it) and an aged/verified default so
# the rated gate can be exercised. agents carries every column admission and the
# demo lookup read; is_demo_opponent is added by V71's ALTER.
BASE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = BASE_SCHEMA + ";" + ";".join(
        (MIGRATIONS / name).read_text() for name in _MIG_FILES
    )
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
async def task_pool(db_session) -> None:
    """Seed a full fresh pool so a demo (or normal) challenge can bind (V67)."""
    repo = BattleRepository(db_session)
    for i in range(24):
        await repo.create_task(
            source=TaskSource.GENERATED,
            category="general",
            title="Write a parser",
            prompt=f"Parse this log format. (variant {i})",
            rubric=RUBRIC,
            time_limit_seconds=600,
            created_by_user_id=None,
        )
    await db_session.commit()


# --- construction helpers ---------------------------------------------------


async def _owner(session, *, verified: bool = True, aged: bool = True) -> str:
    """An owner aged well past the 7-day rated minimum and verified by default."""
    uid = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO users (id, email, is_verified, created_at) "
            "VALUES (CAST(:id AS UUID), :e, :v, "
            "        now() - make_interval(days => :age))"
        ),
        {"id": uid, "e": f"o-{uid[:8]}@example.test", "v": verified, "age": 400 if aged else 0},
    )
    return uid


async def _agent(session, owner: str, *, demo: bool = False) -> str:
    aid = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO agents (id, handle, name, owner_user_id, is_active, "
            "is_hosted, available_for_battles, is_demo_opponent, battle_elo) "
            "VALUES (CAST(:id AS UUID), :h, 'F', CAST(:o AS UUID), TRUE, FALSE, "
            "        TRUE, :demo, :elo)"
        ),
        {"id": aid, "h": f"f-{aid[:8]}", "o": owner, "demo": demo, "elo": DEFAULT_ELO},
    )
    return aid


async def _make_demo_challenge(session, *, is_demo: bool = True) -> tuple[str, str, str, str, str]:
    """A challenge_pending battle: user agent_a vs the demo opponent agent_b.

    Returns (battle_id, agent_a, agent_b, owner_a, owner_b). ``owner_b`` owns the
    demo opponent (it stands in for the admin). Built through the real
    repo.create_challenge so every admission predicate (pool, eligibility) is
    exercised — the only thing this skips is the service-layer Redis rate limit.
    """
    owner_a = await _owner(session)
    owner_b = await _owner(session)
    agent_a = await _agent(session, owner_a)
    # agent_b is an ORDINARY agent: the auto-drive keys on the BATTLE's is_demo
    # flag, never on agents.is_demo_opponent (that flag only serves the endpoint's
    # get_demo_opponent lookup, and the single-demo-opponent unique index would
    # reject a second one anyway). So the battle-level demo behaviour is exercised
    # without minting a demo-opponent agent per test.
    agent_b = await _agent(session, owner_b)
    repo = BattleRepository(session)
    battle_id = await repo.create_challenge(
        task_category="general",
        task_difficulty=None,
        agent_a_id=agent_a,
        agent_a_owner_snapshot=owner_a,
        challenge_ttl_seconds=3600,
        target_cap=5,
        agent_b_id=agent_b,
        agent_b_owner_snapshot=owner_b,
        is_demo=is_demo,
    )
    assert battle_id is not None
    await session.commit()
    return battle_id, agent_a, agent_b, owner_a, owner_b


async def _to_judging(
    session, battle_id: str, agent_a: str, agent_b: str, owner_b: str, votes: list[Vote]
) -> str:
    """Accept via the REAL service (so the demo rated gate runs), then drive to
    judging through the state machine. Returns the running-phase lease token.

    Acceptance goes through BattleService.accept on purpose: that is where
    _decide_rated_eligibility fires, so a demo battle records rated_eligible=FALSE
    / reason 'demo' exactly as production would. The later transitions use the
    state-machine primitives — this test is about the RATED verdict, not the
    reconciler wiring (that is the auto-drive test).
    """
    repo = BattleRepository(session)
    events = AgentEventRepository(session)

    accepted = await BattleService(session).accept(battle_id, owner_b)
    assert accepted is not None
    await repo.reserve_both(battle_id, agent_a, agent_b, 600)
    ev_a = await events.create(agent_a, "battle_ready_check", {}, ttl_seconds=60)
    ev_b = await events.create(agent_b, "battle_ready_check", {}, ttl_seconds=60)
    row = await repo.arm_readiness(battle_id, ev_a, ev_b, 60)
    assert await repo._mark_queued(battle_id, row["readiness_generation"]) is not None
    token = str(uuid.uuid4())
    assert await repo._mark_running(battle_id, token, 600) is not None
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
    return token


@contextmanager
def _no_transport():
    queued = AsyncMock(return_value=DeliveryResult.QUEUED)
    with (
        patch("app.services.battle_service.dispatch_existing", queued),
        patch("app.services.battle_runner.dispatch_existing", queued),
    ):
        yield


async def _elo(session_maker, agent_id: str) -> int:
    async with session_maker() as session:
        row = await session.execute(
            text("SELECT battle_elo FROM agents WHERE id = CAST(:id AS UUID)"),
            {"id": agent_id},
        )
        return int(row.scalar_one())


# ---------------------------------------------------------------------------
# Rating suppression — the mutation target.
# ---------------------------------------------------------------------------


class TestDemoRatingSuppression:
    async def test_demo_battle_settles_unrated_across_distinct_owners(
        self, session_maker, db_session, task_pool
    ) -> None:
        """A demo battle between TWO DIFFERENT verified, aged owners — which the
        rated path would otherwise rate — settles UNRATED, Elo untouched.

        MUTATION: delete ``if is_demo: return (False, None, "demo")`` from
        BattleService._decide_rated_eligibility. The owners are distinct, verified
        and aged and within quota, so the gate would then return eligible, accept
        would freeze rated_eligible=TRUE, and settle would move Elo — this test's
        is_rated / reason / Elo assertions all go red.
        """
        battle_id, agent_a, agent_b, _, owner_b = await _make_demo_challenge(db_session)

        # The frozen verdict is written at ACCEPT time.
        token = await _to_judging(
            db_session, battle_id, agent_a, agent_b, owner_b, votes=[Vote.A, Vote.A, Vote.A]
        )
        async with session_maker() as session:
            row = await BattleRepository(session).get(battle_id)
        assert row["rated_eligible"] is False
        assert row["rated_ineligibility_reason"] == "demo"

        before_a, before_b = await _elo(session_maker, agent_a), await _elo(session_maker, agent_b)
        async with session_maker() as session:
            change = await BattleRunner(session, gate=None).settle_battle(battle_id, token)
        assert change is not None
        assert not change.applied

        async with session_maker() as session:
            settled = await BattleRepository(session).get(battle_id)
        assert settled["status"] == "completed"
        assert settled["is_rated"] is False
        assert await _elo(session_maker, agent_a) == before_a
        assert await _elo(session_maker, agent_b) == before_b

    async def test_normal_cross_owner_battle_still_rates(
        self, session_maker, db_session, task_pool
    ) -> None:
        """The demo rule must not break the ordinary rated path: a NON-demo
        cross-owner battle between verified, aged owners still moves Elo.
        """
        battle_id, agent_a, agent_b, _, owner_b = await _make_demo_challenge(
            db_session, is_demo=False
        )
        token = await _to_judging(
            db_session, battle_id, agent_a, agent_b, owner_b, votes=[Vote.A, Vote.A, Vote.A]
        )
        async with session_maker() as session:
            row = await BattleRepository(session).get(battle_id)
        assert row["rated_eligible"] is True
        assert row["rated_ineligibility_reason"] is None

        before_a = await _elo(session_maker, agent_a)
        async with session_maker() as session:
            change = await BattleRunner(session, gate=None).settle_battle(battle_id, token)
        assert change is not None and change.applied

        async with session_maker() as session:
            settled = await BattleRepository(session).get(battle_id)
        assert settled["is_rated"] is True
        # Winner A (unanimous) gained Elo — the rated path is intact.
        assert await _elo(session_maker, agent_a) > before_a


# ---------------------------------------------------------------------------
# Auto-drive — the demo side reaches a judged result with no human action.
# ---------------------------------------------------------------------------


class TestDemoAutoDrive:
    async def test_demo_opponent_auto_accepts_acks_and_submits_to_completion(
        self, session_maker, db_session, task_pool
    ) -> None:
        """A demo battle walks challenge_pending -> completed driven ONLY by the
        reconciler on the demo side: the platform opponent auto-accepts,
        auto-ACKs readiness and auto-submits a REAL (mocked) answer. The only
        human-side action synthesized here is the USER agent's own ACK + final,
        which in production its agent performs via the API.
        """
        battle_id, agent_a, agent_b, _, _ = await _make_demo_challenge(db_session)

        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        judge = AsyncMock(return_value=VALID_JUDGE_REPLY)

        # -- pass 1: auto-accept (demo side) + arm -> reserved -----------------
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", judge):
            counts = await drive()
        assert counts["demo_accepted"] == 1, counts
        async with session_maker() as session:
            b = await BattleRepository(session).get(battle_id)
        assert b["status"] == "reserved"
        # The demo side was consented for with no human action.
        assert b["agent_b_accepted_at"] is not None
        assert b["is_demo"] is True

        # The demo side's ready-check is already ACKed for it; the USER side is not.
        async with session_maker() as session:
            evs = {
                str(r["target_agent_id"]): r["acked_at"]
                for r in (
                    await session.execute(
                        text(
                            "SELECT target_agent_id, acked_at FROM agent_events "
                            "WHERE type = 'battle_ready_check' AND target_agent_id "
                            "IN (CAST(:a AS UUID), CAST(:b AS UUID))"
                        ),
                        {"a": agent_a, "b": agent_b},
                    )
                ).mappings()
            }
        assert evs[agent_b] is not None, "demo side auto-ACKed"
        assert evs[agent_a] is None, "user side is driven normally, not auto-ACKed"

        # -- user agent ACKs its own side, then lapse the bind lease ----------
        async with session_maker() as session:
            await AgentEventRepository(session).mark_acked(
                agent_a, [str(b["ready_check_event_id_a"])]
            )
            await session.execute(
                text(
                    "UPDATE battles SET lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        # -- pass 2: reserved -> queued -> running, demo answer submitted ------
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", judge):
            counts = await drive()
        assert counts["queued"] == 1, counts
        assert counts["started"] == 1, counts
        assert counts["demo_submitted"] == 1, counts

        async with session_maker() as session:
            b = await BattleRepository(session).get(battle_id)
            subs = await BattleRepository(session).list_submissions(battle_id)
        assert b["status"] == "running"
        demo_finals = [s for s in subs if str(s["side"]) == Side.B.value and s["is_final"]]
        assert len(demo_finals) == 1, "demo opponent submitted exactly one final"
        assert demo_finals[0]["truncated"] is False, "a real answer, not deadline silence"
        assert demo_finals[0]["content"], "the answer has content"

        # -- user agent submits its final, then judge + settle -----------------
        # start_queued holds a 300s battle lease; clear it so the next pass's
        # running phase can claim the row (in production the tick simply waits it
        # out; a back-to-back test pass must lapse it explicitly).
        async with session_maker() as session:
            assert await BattleRepository(session).add_submission(
                battle_id, Side.A, 1, "my answer", is_final=True
            )
            await session.execute(
                text(
                    "UPDATE battles SET lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        with _no_transport(), patch("app.services.battle_runner.call_judge_model", judge):
            await drive()

        async with session_maker() as session:
            b = await BattleRepository(session).get(battle_id)
        assert b["status"] == "completed", "reached a judged result"
        assert b["is_rated"] is False, "a demo battle never rates"

    async def test_demo_opponent_never_declines(
        self, session_maker, db_session, task_pool
    ) -> None:
        """The demo side only ever CONSENTS. auto_accept_demo produces 'accepted',
        never 'declined', and leaves no block/cooldown against the challenger — so
        the demo battle can never be stalled by the opponent refusing.
        """
        battle_id, agent_a, agent_b, _, owner_b = await _make_demo_challenge(db_session)

        async with session_maker() as session:
            accepted = await BattleService(session).auto_accept_demo(battle_id)
            assert accepted is not None
            await session.commit()

        async with session_maker() as session:
            b = await BattleRepository(session).get(battle_id)
            blocks = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM battle_blocks WHERE "
                        "blocker_owner_user_id = CAST(:o AS UUID)"
                    ),
                    {"o": owner_b},
                )
            ).scalar_one()
            cooldowns = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM battle_challenge_cooldowns WHERE "
                        "target_agent_id = CAST(:a AS UUID)"
                    ),
                    {"a": agent_a},
                )
            ).scalar_one()
        assert b["status"] == "accepted"
        assert blocks == 0 and cooldowns == 0

    async def test_auto_drive_is_idempotent_no_double_submit(
        self, session_maker, db_session, task_pool
    ) -> None:
        """A second drive_demo_submission over an already-answered running demo
        battle (two workers racing the queued->running step) does NOT double-submit
        and does NOT spend a second answer call — it sees the existing final and
        returns without calling the provider.
        """
        battle_id, agent_a, agent_b, _, _ = await _make_demo_challenge(db_session)
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        judge = AsyncMock(return_value=VALID_JUDGE_REPLY)

        # Walk to running with the demo's answer submitted once.
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", judge):
            await drive()
        async with session_maker() as session:
            b = await BattleRepository(session).get(battle_id)
            await AgentEventRepository(session).mark_acked(
                agent_a, [str(b["ready_check_event_id_a"])]
            )
            await session.execute(
                text(
                    "UPDATE battles SET lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", judge):
            await drive()

        async with session_maker() as session:
            subs = await BattleRepository(session).list_submissions(battle_id)
        demo_finals = [s for s in subs if str(s["side"]) == Side.B.value and s["is_final"]]
        assert len(demo_finals) == 1, "demo submitted exactly one final on the first pass"

        # Drive the demo submission a SECOND time directly, as a racing worker
        # would: the guard must short-circuit before the provider is touched.
        second = AsyncMock(return_value=VALID_JUDGE_REPLY)
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            running = await runner.repo.get(battle_id)
            with patch("app.services.battle_runner.call_judge_model", second):
                result = await runner.drive_demo_submission(running, "k", "http://u")
        assert result is False, "the second drive is a no-op"
        assert second.await_count == 0, "no second answer call was spent"

        async with session_maker() as session:
            subs = await BattleRepository(session).list_submissions(battle_id)
        demo_finals = [s for s in subs if str(s["side"]) == Side.B.value and s["is_final"]]
        assert len(demo_finals) == 1, "still exactly one demo final"


# ---------------------------------------------------------------------------
# Schema guarantee + inline acceptance (the follow-up fixes).
# ---------------------------------------------------------------------------


class TestDemoSchemaAndCreation:
    async def test_unique_index_rejects_a_second_demo_opponent(
        self, session_maker, db_session, task_pool
    ) -> None:
        """The partial unique index makes a SECOND is_demo_opponent=TRUE agent
        impossible by construction — so get_demo_opponent's single-row guarantee
        holds at the schema level, not just by the seed's ON CONFLICT.
        """
        async with session_maker() as session:
            owner = await _owner(session)
            await _agent(session, owner, demo=True)  # the one demo opponent
            await session.commit()

        with pytest.raises(IntegrityError):
            async with session_maker() as session:
                owner2 = await _owner(session)
                await _agent(session, owner2, demo=True)  # a second one is rejected
                await session.commit()

    async def test_demo_battle_is_accepted_immediately_on_creation(
        self, session_maker, db_session, task_pool
    ) -> None:
        """create_demo_battle folds the opponent's consent into creation: the
        battle is 'accepted' the instant it exists — never 'challenge_pending' —
        so a demo user waits for no reconciler tick and the battle never presses
        on TARGET_CHALLENGE_CAP. The rated verdict is still frozen (False, 'demo').
        """
        async with session_maker() as session:
            owner_a = await _owner(session)
            owner_b = await _owner(session)
            agent_a = await _agent(session, owner_a)
            # An ordinary agent stands in for the resolved demo opponent — the
            # inline accept keys on the battle's is_demo flag, not this agent's.
            demo_agent = await _agent(session, owner_b)
            await session.commit()

            svc = BattleService(session)
            # The service create path calls the Redis-backed challenger rate
            # limiter, which the integration DB fixture has no Redis for; it is not
            # what is under test, so stub it to a no-op.
            with patch.object(
                BattleService, "_check_challenger_rate_limit", AsyncMock(return_value=None)
            ):
                battle_id = await svc.create_demo_battle(
                    agent_a_id=agent_a,
                    challenger_owner_user_id=owner_a,
                    demo_agent_id=demo_agent,
                    task_category="general",
                    task_difficulty=None,
                )
            await session.commit()

        async with session_maker() as session:
            b = await BattleRepository(session).get(battle_id)
        assert b["status"] == "accepted", "inline accept — never left challenge_pending"
        assert b["is_demo"] is True
        assert b["agent_b_accepted_at"] is not None
        assert b["rated_eligible"] is False
        assert b["rated_ineligibility_reason"] == "demo"
