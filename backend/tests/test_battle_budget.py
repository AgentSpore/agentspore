"""V68 Track-3 section B — judge-call budget cap, ledger, and settle-UNRATED.

These run the REAL migrations (V65..V68) against testcontainers Postgres and the
REAL runner judging path with a REAL BattleJudgeBudgetService, so the budget
mechanism is proven end-to-end rather than mocked. Battle construction reuses the
runner suite's state-machine helper (a hand-built 'judging' row could satisfy the
reservation guard while being unreachable).
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_battle_runner import (
    BASE_SCHEMA,
    DEFAULT_ELO,
    RUBRIC,
    V65_PATH,
    V66_PATH,
    V67_PATH,
    V68_PATH,
    TaskSource,
    _battle_in_judging,
    _elo,
)
from testcontainers.postgres import PostgresContainer

from app.core.config import get_settings
from app.repositories.battle_repo import BattleRepository
from app.services.battle_budget import BattleJudgeBudgetService
from app.services.battle_runner import BattleRunner, _judge_and_settle

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_VALID_JUDGE = (
    '{"vote": "submission_alpha", "confidence": 0.9, "reasoning": "ok", '
    '"scores": {"correctness": 1.0}}'
)


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = (
        f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()};"
        f"{V67_PATH.read_text()};{V68_PATH.read_text()}"
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
async def task_id(db_session) -> str:
    uid = str(uuid.uuid4())
    await db_session.execute(
        text("INSERT INTO users (id, email) VALUES (CAST(:id AS UUID), :e)"),
        {"id": uid, "e": f"o-{uid[:8]}@example.test"},
    )
    repo = BattleRepository(db_session)
    tid = await repo.create_task(
        source=TaskSource.GENERATED, category="general", title="T",
        prompt="Parse this.", rubric=RUBRIC, time_limit_seconds=600,
        created_by_user_id=None,
    )
    for i in range(24):
        await repo.create_task(
            source=TaskSource.GENERATED, category="general", title="T",
            prompt=f"Parse this. (variant {i})", rubric=RUBRIC,
            time_limit_seconds=600, created_by_user_id=None,
        )
    await db_session.commit()
    return tid


async def _stamp_live_battle_lease(session_maker, battle_id: str, token: str) -> None:
    async with session_maker() as s:
        await s.execute(
            text(
                "UPDATE battles SET lease_token = CAST(:t AS UUID), "
                "lease_expires_at = NOW() + INTERVAL '5 minutes' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"t": token, "b": battle_id},
        )
        await s.commit()


async def _owners_of(session_maker, battle_id: str) -> tuple[str, str]:
    async with session_maker() as s:
        row = (
            await s.execute(
                text(
                    "SELECT agent_a_owner_snapshot, agent_b_owner_snapshot "
                    "FROM battles WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
        ).mappings().one()
    return str(row["agent_a_owner_snapshot"]), str(row["agent_b_owner_snapshot"])


async def _prefill_owner_budget(session_maker, owner: str, calls: int) -> None:
    async with session_maker() as s:
        await s.execute(
            text(
                "INSERT INTO battle_judge_owner_daily_usage "
                "(budget_day, owner_user_id, reserved_calls) "
                "VALUES (:d, CAST(:o AS UUID), :c)"
            ),
            {"d": date.today(), "o": owner, "c": calls},
        )
        await s.commit()


async def _battle_state(session_maker, battle_id: str) -> dict:
    async with session_maker() as s:
        return await BattleRepository(s).get(battle_id)


async def _ledger_count(session_maker, battle_id: str) -> int:
    async with session_maker() as s:
        return int(
            (
                await s.execute(
                    text(
                        "SELECT COUNT(*) FROM battle_judge_call_ledger "
                        "WHERE battle_id = CAST(:b AS UUID)"
                    ),
                    {"b": battle_id},
                )
            ).scalar_one()
        )


class TestBudgetExhaustionSettlesUnrated:
    """G1/B3: an over-budget rated battle completes UNRATED, not stranded."""

    async def test_owner_budget_exhausted_completes_unrated_no_provider_call(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, _, token = await _battle_in_judging(
            db_session, task_id, votes=[]
        )
        owner_a, _ = await _owners_of(session_maker, battle_id)
        # Fill the owner's daily budget to the ceiling so the very first
        # reservation is refused before any provider request.
        await _prefill_owner_budget(
            session_maker, owner_a,
            get_settings().battle_judge_owner_daily_call_limit,
        )
        await _stamp_live_battle_lease(session_maker, battle_id, token)

        budget = BattleJudgeBudgetService(session_maker)
        counts = {"settled": 0}
        # call_judge_model must never fire — the reservation refuses first.
        with patch(
            "app.services.battle_runner.call_judge_model",
            AsyncMock(return_value=_VALID_JUDGE),
        ) as mock_call:
            await _judge_and_settle(
                session_maker, None, battle_id, token, "k", "http://u", counts, budget
            )

        mock_call.assert_not_called()
        battle = await _battle_state(session_maker, battle_id)
        assert battle["status"] == "completed"
        assert battle["is_rated"] is False
        assert battle["judging_stop_reason"] == "owner_budget_exhausted"
        assert battle["winner"] is None
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO
        # Refused before insert: no unit was ever spent for this battle.
        assert await _ledger_count(session_maker, battle_id) == 0
        assert counts["settled"] == 1


class TestProductCap:
    """B2/G3: the 12-attempt product cap is authoritative across reclaims."""

    async def test_battle_at_twelve_ledger_rows_is_refused(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])
        owner_a, owner_b = await _owners_of(session_maker, battle_id)
        await _stamp_live_battle_lease(session_maker, battle_id, token)

        # Seed 12 ledger rows against a real judge run for this battle, as if the
        # panel had already spent its whole product budget over prior reclaims.
        async with session_maker() as s:
            repo = BattleRepository(s)
            run_id = await repo.create_judge_run(
                battle_id=battle_id, judge_kind="llm", judge_ref="zai/glm-4.5-flash",
                replicate_seed="seed-cap", presented_order="ab",
            )
            for n in range(1, 13):
                await s.execute(
                    text(
                        "INSERT INTO battle_judge_call_ledger "
                        "(battle_id, judge_run_id, owner_a_user_id, owner_b_user_id, "
                        " budget_day, provider_attempt_no, provider, model, status, "
                        " finished_at) "
                        "VALUES (CAST(:b AS UUID), CAST(:r AS UUID), CAST(:oa AS UUID), "
                        " CAST(:ob AS UUID), :d, :n, 'zai', 'm', 'failed', NOW())"
                    ),
                    {"b": battle_id, "r": run_id, "oa": owner_a, "ob": owner_b,
                     "d": date.today(), "n": n},
                )
            await s.commit()

        budget = BattleJudgeBudgetService(session_maker)
        # A fresh running judge run to reserve against.
        async with session_maker() as s:
            repo = BattleRepository(s)
            run2 = await repo.create_judge_run(
                battle_id=battle_id, judge_kind="llm", judge_ref="zai/glm-4.5-flash",
                replicate_seed="seed-new", presented_order="ab",
            )
            run_token = str(uuid.uuid4())
            await repo.claim_judge_run(run2, run_token, 180)
            await s.commit()

        result = await budget.reserve_call(
            battle_id=battle_id, judge_run_id=run2,
            battle_lease_token=token, run_lease_token=run_token,
            owner_a_user_id=owner_a, owner_b_user_id=owner_b,
            provider="zai", model="m",
        )
        assert result.granted is False
        assert result.reason == "battle_attempt_cap"
        assert await _ledger_count(session_maker, battle_id) == 12  # no 13th row


class TestStaleLease:
    """G5: a worker that lost either lease cannot reserve a call unit."""

    async def test_wrong_battle_lease_is_refused_without_spending(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])
        owner_a, owner_b = await _owners_of(session_maker, battle_id)
        await _stamp_live_battle_lease(session_maker, battle_id, token)

        async with session_maker() as s:
            repo = BattleRepository(s)
            run_id = await repo.create_judge_run(
                battle_id=battle_id, judge_kind="llm", judge_ref="zai/glm-4.5-flash",
                replicate_seed="seed-stale", presented_order="ab",
            )
            run_token = str(uuid.uuid4())
            await repo.claim_judge_run(run_id, run_token, 180)
            await s.commit()

        budget = BattleJudgeBudgetService(session_maker)
        result = await budget.reserve_call(
            battle_id=battle_id, judge_run_id=run_id,
            battle_lease_token=str(uuid.uuid4()),  # NOT the live lease
            run_lease_token=run_token,
            owner_a_user_id=owner_a, owner_b_user_id=owner_b,
            provider="zai", model="m",
        )
        assert result.granted is False
        assert result.reason == "stale_lease"
        assert await _ledger_count(session_maker, battle_id) == 0


class TestCrossBattleRunGuard:
    """F1: a judge run from ANOTHER battle cannot authorize a spend here."""

    async def test_run_from_a_different_battle_is_refused(
        self, session_maker, db_session, task_id
    ) -> None:
        # Two independent judging battles.
        battle_x, _, _, token_x = await _battle_in_judging(db_session, task_id, votes=[])
        battle_y, _, _, token_y = await _battle_in_judging(db_session, task_id, votes=[])
        ox_a, ox_b = await _owners_of(session_maker, battle_y)
        await _stamp_live_battle_lease(session_maker, battle_x, token_x)
        await _stamp_live_battle_lease(session_maker, battle_y, token_y)

        # A live, claimed run that belongs to battle X.
        async with session_maker() as s:
            repo = BattleRepository(s)
            run_x = await repo.create_judge_run(
                battle_id=battle_x, judge_kind="llm", judge_ref="zai/glm-4.5-flash",
                replicate_seed="seed-x", presented_order="ab",
            )
            run_token = str(uuid.uuid4())
            await repo.claim_judge_run(run_x, run_token, 180)
            await s.commit()

        budget = BattleJudgeBudgetService(session_maker)
        # Try to spend for battle Y using battle X's run: the run is running with
        # the right token, but it does NOT belong to Y — the r.battle_id guard
        # must refuse it (revert the guard -> this GRANTS -> RED).
        result = await budget.reserve_call(
            battle_id=battle_y, judge_run_id=run_x,
            battle_lease_token=token_y, run_lease_token=run_token,
            owner_a_user_id=ox_a, owner_b_user_id=ox_b,
            provider="zai", model="m",
        )
        assert result.granted is False
        assert result.reason == "stale_lease"
        assert await _ledger_count(session_maker, battle_y) == 0


class TestReservationCleanup:
    """F7: an unexpected exception never leaves a ledger row stuck 'reserved'."""

    async def test_unexpected_call_error_settles_the_reservation_failed(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, _, _, token = await _battle_in_judging(db_session, task_id, votes=[])
        await _stamp_live_battle_lease(session_maker, battle_id, token)
        budget = BattleJudgeBudgetService(session_maker)

        # A non-transport exception (not JudgeTransportError) escapes the inner
        # handler; the outer finally must still settle the reserved row failed.
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.call_judge_model",
                AsyncMock(side_effect=ValueError("boom")),
            ):
                with pytest.raises(ValueError):
                    await runner.run_judge_panel(
                        battle_id, "k", "http://u", token, budget=budget
                    )

        async with session_maker() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT status FROM battle_judge_call_ledger "
                        "WHERE battle_id = CAST(:b AS UUID)"
                    ),
                    {"b": battle_id},
                )
            ).scalars().all()
        assert rows == ["failed"]  # reserved -> settled, never left dangling


class TestBreakerHaltsJudgingNotLifecycle:
    """B5: an open breaker pauses judging (battle stays 'judging'), never settles."""

    async def test_open_breaker_leaves_the_battle_judging(
        self, session_maker, db_session, task_id
    ) -> None:
        battle_id, agent_a, _, token = await _battle_in_judging(
            db_session, task_id, votes=[]
        )
        await _stamp_live_battle_lease(session_maker, battle_id, token)
        budget = BattleJudgeBudgetService(session_maker)
        counts = {"settled": 0}

        # Breaker open + a provider mock that WOULD answer: proves the breaker,
        # not a provider failure, is what pauses judging. Removing the pre-reserve
        # breaker check (mutation) lets the panel judge+settle -> battle completed
        # -> this assertion RED.
        with patch(
            "app.services.battle_runner.breaker_is_open",
            AsyncMock(return_value=True),
        ), patch(
            "app.services.battle_runner.call_judge_model",
            AsyncMock(return_value=_VALID_JUDGE),
        ) as mock_call:
            await _judge_and_settle(
                session_maker, None, battle_id, token, "k", "http://u", counts, budget
            )

        mock_call.assert_not_called()
        battle = await _battle_state(session_maker, battle_id)
        # Still judging — NOT completed, NOT stranded — for a later pass.
        assert battle["status"] == "judging"
        assert battle["is_rated"] is None
        assert battle["judging_stop_reason"] is None
        assert counts["settled"] == 0
        assert await _ledger_count(session_maker, battle_id) == 0
        assert await _elo(session_maker, agent_a) == DEFAULT_ELO
