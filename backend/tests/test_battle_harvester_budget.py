"""Harvester budget against a REAL Postgres, with the real V68/V70/V72/V78 CHECKs.

The rest of the harvester suite doubles reserve_budget/settle_budget on the
instance, which is why a settle that violated the V68
``battle_judge_call_finished_agrees`` CHECK sat green through 1178 tests: an
AsyncMock accepts any UPDATE, a table with a constraint does not. These tests
run the reserve/settle round-trip through the real service and the real table.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_battle_runner import (
    BASE_SCHEMA,
    MIGRATIONS,
    V65_PATH,
    V66_PATH,
    V67_PATH,
    V68_PATH,
    V70_PATH,
    V71_PATH,
    V72_PATH,
)
from testcontainers.postgres import PostgresContainer

from app.services.battle_budget import BattleJudgeBudgetService

pytestmark = pytest.mark.asyncio(loop_scope="module")

V78_PATH = MIGRATIONS / "V78__harvester_ledger_kind.sql"


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(pg_container):
    async_url = pg_container.get_connection_url().replace("psycopg2", "asyncpg")
    eng = create_async_engine(async_url, future=True)
    sql = (
        f"{BASE_SCHEMA};{V65_PATH.read_text()};{V66_PATH.read_text()};"
        f"{V67_PATH.read_text()};{V68_PATH.read_text()};{V70_PATH.read_text()};"
        f"{V71_PATH.read_text()};{V72_PATH.read_text()};{V78_PATH.read_text()}"
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


class TestHarvestReservation:
    async def test_reserve_then_settle_survives_the_ledger_checks(self, session_maker):
        """The round-trip the mocked suite could not exercise.

        Settling without finished_at violates battle_judge_call_finished_agrees
        (V68), and a ledger row with kind='harvest' carrying any identifying
        column violates battle_judge_call_kind_shape (V78). Both are enforced by
        the table, not by the code, so only a real table can prove them.
        """
        budget = BattleJudgeBudgetService(session_maker)

        reservation = await budget.reserve_harvest_call(
            provider="zai", model="zai/glm-4.5-flash"
        )
        assert reservation.granted
        assert reservation.ledger_id is not None

        async with session_maker() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT kind, status, finished_at, battle_id, judge_run_id,
                               owner_a_user_id, owner_b_user_id, submitter_user_id
                        FROM battle_judge_call_ledger
                        WHERE id = CAST(:id AS UUID)
                        """
                    ),
                    {"id": reservation.ledger_id},
                )
            ).mappings().one()
        assert row["kind"] == "harvest"
        assert row["status"] == "reserved"
        assert row["finished_at"] is None
        # The whole point of the fourth shape: no owner is charged for a topic
        # that came from an open source rather than an account.
        assert row["battle_id"] is None
        assert row["judge_run_id"] is None
        assert row["owner_a_user_id"] is None
        assert row["owner_b_user_id"] is None
        assert row["submitter_user_id"] is None

        await budget.settle_call(reservation.ledger_id, succeeded=True)

        async with session_maker() as session:
            settled = (
                await session.execute(
                    text(
                        """
                        SELECT status, finished_at FROM battle_judge_call_ledger
                        WHERE id = CAST(:id AS UUID)
                        """
                    ),
                    {"id": reservation.ledger_id},
                )
            ).mappings().one()
        # A settle that left finished_at NULL would have raised on the CHECK
        # rather than reaching here; assert the value so a future settle that
        # stops setting it fails loudly instead of silently.
        assert settled["status"] == "succeeded"
        assert settled["finished_at"] is not None

    async def test_reservation_increments_the_global_counter(self, session_maker):
        """Harvest spend lands on the SAME counter judging and validation use.

        A private counter would mean the global daily cap is not a cap.
        """
        budget = BattleJudgeBudgetService(session_maker)

        async with session_maker() as session:
            before = (
                await session.execute(
                    text(
                        "SELECT COALESCE(SUM(reserved_calls), 0) "
                        "FROM battle_judge_global_daily_usage"
                    )
                )
            ).scalar_one()

        reservation = await budget.reserve_harvest_call(
            provider="zai", model="zai/glm-4.5-flash"
        )
        assert reservation.granted

        async with session_maker() as session:
            after = (
                await session.execute(
                    text(
                        "SELECT COALESCE(SUM(reserved_calls), 0) "
                        "FROM battle_judge_global_daily_usage"
                    )
                )
            ).scalar_one()
        assert after == before + 1
