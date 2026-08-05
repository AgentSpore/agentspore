"""Tests for contender ratings (V73) — the rating a PLATFORM fighter carries.

Integration against the real V66-V73 migrations on testcontainers Postgres, for
the same reason as every other battle test: what is under test is settlement
inside one transaction plus the CHECKs the migration adds, and a mock of either
would only prove the mock was told the right answer.

What each test falsifies is stated on it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.database import get_db
from app.main import app
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import Side
from app.services.battle_judges import PanelVerdict
from app.services.battle_runner import BattleRunner
from app.services.battle_service import BattleService

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
_MIG_FILES = [
    "V65__agent_events.sql",
    "V66__battles.sql",
    "V67__battle_task_secrecy.sql",
    "V68__battle_anti_abuse.sql",
    "V69__battle_injection_stop_reason.sql",
    "V70__battle_user_tasks.sql",
    "V71__battle_demo_mode.sql",
    "V72__battle_contenders.sql",
    "V73__contender_rating.sql",
]

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


async def _contender_ids(session) -> list[str]:
    rows = await BattleRepository(session).list_enabled_contenders()
    return [str(r["id"]) for r in rows]


async def _elo(session, contender_id: str) -> dict:
    result = await session.execute(
        text(
            "SELECT elo, wins, losses, ties FROM battle_contenders "
            "WHERE id = CAST(:c AS UUID)"
        ),
        {"c": contender_id},
    )
    return dict(result.mappings().one())


async def _agent(session, owner_id: str | None = None) -> str:
    if owner_id is None:
        owner = await session.execute(
            text("INSERT INTO users (email) VALUES (:e) RETURNING id"),
            {"e": f"{uuid.uuid4()}@example.test"},
        )
        owner_id = str(owner.scalar_one())
    result = await session.execute(
        text(
            "INSERT INTO agents (handle, name, owner_user_id) "
            "VALUES (:h, :h, CAST(:o AS UUID)) RETURNING id"
        ),
        {"h": f"a-{uuid.uuid4().hex[:8]}", "o": owner_id},
    )
    return str(result.scalar_one())


async def _judging_battle(session, *, sides: dict, stop_reason: str | None = None) -> str:
    """A battle parked in 'judging' with a live lease, ready to settle."""
    columns = ", ".join(sides)
    values = ", ".join(f"CAST(:{key} AS UUID)" for key in sides)
    result = await session.execute(
        text(
            f"""
            INSERT INTO battles ({columns}, status, started_at, rated_eligible,
                                 judging_stop_reason, lease_token, lease_expires_at,
                                 challenge_expires_at, agent_b_accepted_at,
                                 deadline_at, queued_at, task_id,
                                 task_title_snapshot, task_prompt_snapshot,
                                 task_rubric_snapshot, time_limit_seconds_snapshot,
                                 rated_quota_day)
            SELECT {values}, 'judging', NOW(), :rated_eligible, :stop_reason,
                   CAST(:lease AS UUID), NOW() + INTERVAL '10 minutes',
                   NOW() + INTERVAL '1 hour', NOW(), NOW() + INTERVAL '5 minutes',
                   NOW(), t.id, t.title, t.prompt, t.rubric, t.time_limit_seconds,
                   CASE WHEN :rated_eligible THEN CURRENT_DATE END
              FROM battle_tasks t
             LIMIT 1
            RETURNING id
            """
        ),
        {
            **sides,
            "rated_eligible": bool(sides.get("agent_b_id")),
            "stop_reason": stop_reason,
            "lease": str(uuid.uuid4()),
        },
    )
    battle_id = str(result.scalar_one())
    await session.commit()
    return battle_id


async def _settle(session, battle_id: str, winner: Side | None, *, is_tie=False):
    lease = await session.execute(
        text("SELECT lease_token FROM battles WHERE id = CAST(:b AS UUID)"),
        {"b": battle_id},
    )
    verdict = PanelVerdict(winner=winner, is_tie=is_tie, reason="test", votes=[])
    return await BattleRunner(session, gate=None).settle_battle(
        battle_id, str(lease.scalar_one()), override_verdict=verdict
    )


class TestContenderSettlement:
    async def test_a_decided_contender_battle_moves_both_ratings_zero_sum(
        self, session_maker, db_session
    ) -> None:
        """A contender win must move the winner up and the loser down by the
        SAME magnitude — the rating is transferred, never minted.

        MUTATION: keep the pre-V73 placeholder rating in lock_fighter_ratings
        (DEFAULT_ELO for a contender side) and skip apply_contender_rating. Both
        rows stay at 1200 and every assertion below goes red.
        """
        a_id, b_id = (await _contender_ids(db_session))[:2]
        before_a, before_b = await _elo(db_session, a_id), await _elo(db_session, b_id)

        async with session_maker() as session:
            battle_id = await _judging_battle(
                session, sides={"contender_a_id": a_id, "contender_b_id": b_id}
            )
            change = await _settle(session, battle_id, Side.A)

        assert change is not None and change.applied
        after_a, after_b = await _elo(db_session, a_id), await _elo(db_session, b_id)
        assert after_a["elo"] - before_a["elo"] == before_b["elo"] - after_b["elo"] > 0
        assert after_a["wins"] == before_a["wins"] + 1
        assert after_b["losses"] == before_b["losses"] + 1

        row = await BattleRepository(db_session).get(battle_id)
        assert row["elo_a_before"] == before_a["elo"], "the real rating, not 1200"
        assert row["elo_a_after"] == after_a["elo"]
        assert row["is_rated"] is False, "an auto-battle still never rates AGENT Elo"

    async def test_a_tie_is_zero_sum_and_counts_a_tie_on_both_sides(
        self, session_maker, db_session
    ) -> None:
        """A tie between unequal ratings still moves rating — toward the
        underdog — and increments ``ties`` on both contenders.

        MUTATION: map a tie to 'win' in _outcome_for. The ties assertions go red.
        """
        a_id, b_id = (await _contender_ids(db_session))[:2]
        before_a, before_b = await _elo(db_session, a_id), await _elo(db_session, b_id)

        async with session_maker() as session:
            battle_id = await _judging_battle(
                session, sides={"contender_a_id": a_id, "contender_b_id": b_id}
            )
            await _settle(session, battle_id, None, is_tie=True)

        after_a, after_b = await _elo(db_session, a_id), await _elo(db_session, b_id)
        assert (after_a["elo"] - before_a["elo"]) == -(after_b["elo"] - before_b["elo"])
        assert after_a["ties"] == before_a["ties"] + 1
        assert after_b["ties"] == before_b["ties"] + 1

    async def test_a_stopped_panel_moves_nothing(
        self, session_maker, db_session
    ) -> None:
        """``judging_stop_reason`` means the panel was cut short, so the winner
        it nominally produced is not a result worth rating.

        MUTATION: drop ``not judging_stopped`` from the contender rating gate.
        The equality below goes red.
        """
        a_id, b_id = (await _contender_ids(db_session))[:2]
        before_a, before_b = await _elo(db_session, a_id), await _elo(db_session, b_id)

        async with session_maker() as session:
            battle_id = await _judging_battle(
                session,
                sides={"contender_a_id": a_id, "contender_b_id": b_id},
                stop_reason="global_budget_exhausted",
            )
            change = await _settle(session, battle_id, Side.A)

        assert change is not None and not change.applied
        assert await _elo(db_session, a_id) == before_a
        assert await _elo(db_session, b_id) == before_b

    async def test_agent_vs_agent_still_rates_through_agents_battle_elo(
        self, session_maker, db_session
    ) -> None:
        """The agent rating path is untouched by V73: a rated agent battle still
        writes agents.battle_elo and no contender row moves.

        MUTATION: route every settlement through apply_contender_rating. The
        agent rating stops moving and the first assertion goes red.
        """
        async with session_maker() as session:
            agent_a, agent_b = await _agent(session), await _agent(session)
            await session.commit()
            battle_id = await _judging_battle(
                session, sides={"agent_a_id": agent_a, "agent_b_id": agent_b}
            )
            change = await _settle(session, battle_id, Side.A)

        assert change is not None and change.applied
        elos = await db_session.execute(
            text(
                "SELECT id, battle_elo, battle_wins FROM agents "
                "WHERE id = ANY(CAST(:ids AS UUID[]))"
            ),
            {"ids": [agent_a, agent_b]},
        )
        by_id = {str(r["id"]): r for r in elos.mappings()}
        assert by_id[agent_a]["battle_elo"] > 1200
        assert by_id[agent_a]["battle_wins"] == 1
        assert by_id[agent_b]["battle_elo"] < 1200

        row = await BattleRepository(db_session).get(battle_id)
        assert row["is_rated"] is True


class TestLeaderboardEndpoint:
    async def test_leaderboard_returns_the_published_contract(
        self, session_maker, db_session
    ) -> None:
        """The frontend is written against this exact shape, and the approach
        roll-up must equal the sum of its contenders' records.

        MUTATION: filter the repository query to ``enabled = TRUE``. A retired
        contender vanishes and the roster-completeness assertion goes red.
        """
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battle_contenders SET enabled = FALSE WHERE id = "
                    "(SELECT id FROM battle_contenders ORDER BY created_at LIMIT 1)"
                )
            )
            await session.commit()
            expected_total = int(
                (
                    await session.execute(text("SELECT COUNT(*) FROM battle_contenders"))
                ).scalar_one()
            )

        async def _override():
            async with session_maker() as session:
                yield session

        app.dependency_overrides[get_db] = _override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/battles/leaderboard")
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["contenders"]) == expected_total, "disabled ones included"
        first = body["contenders"][0]
        assert set(first) == {
            "id", "display_name", "provider", "model_id", "approach_key",
            "elo", "wins", "losses", "ties", "battles",
        }
        elos = [c["elo"] for c in body["contenders"]]
        assert elos == sorted(elos, reverse=True), "sorted by elo DESC"
        assert all(
            c["battles"] == c["wins"] + c["losses"] + c["ties"]
            for c in body["contenders"]
        )

        by_approach: dict[str, int] = {}
        for contender in body["contenders"]:
            by_approach.setdefault(contender["approach_key"], 0)
            by_approach[contender["approach_key"]] += contender["wins"]
        assert {a["approach_key"]: a["wins"] for a in body["approaches"]} == by_approach
        wins = [a["wins"] for a in body["approaches"]]
        assert wins == sorted(wins, reverse=True), "approaches sorted by wins DESC"

    async def test_service_battles_count_excludes_undecided_battles(
        self, db_session
    ) -> None:
        """``battles`` counts decided outcomes only, so a void or no-quorum
        battle — which increments no counter — never inflates it.

        MUTATION: count battles from the battles table instead of the counters.
        The voided battle above starts being counted and this goes red.
        """
        board = await BattleService(db_session).contender_leaderboard()
        assert all(
            c.battles == c.wins + c.losses + c.ties for c in board.contenders
        )
        assert sum(a.battles for a in board.approaches) == sum(
            c.battles for c in board.contenders
        )
