"""Tests for the V73 contender-Elo backfill (``scripts/backfill_contender_elo``).

The script is a one-shot run by hand against prod, so nothing else will ever
exercise it before it runs for real. It is covered here against the real
migrations on testcontainers Postgres because what is under test is a LOCK, a
reset, a replay and a rollback inside one transaction — a mocked session would
only prove the mock was told the right answer, and the first defect this file
found (an ORDER BY on a column ``battles`` does not have) is invisible to any
test that does not reach a real planner.

What each test falsifies is stated on it.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_battle_contender_rating import _MIG_FILES, BASE_SCHEMA, MIGRATIONS
from testcontainers.postgres import PostgresContainer

from app.core.rating import DEFAULT_ELO
from app.repositories.battle_repo import BattleRepository
from scripts import backfill_contender_elo as script

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

DEFAULT_RECORD = (DEFAULT_ELO, 0, 0, 0)


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
async def clean_slate(session_maker):
    """Every test owns the whole roster, so both tables start empty of history."""
    async with session_maker() as session:
        await session.execute(text("DELETE FROM battles"))
        await session.execute(
            text(
                "UPDATE battle_contenders "
                "SET elo = DEFAULT, wins = DEFAULT, losses = DEFAULT, ties = DEFAULT"
            )
        )
        await session.commit()
    yield


async def _snapshot(session_maker) -> dict[str, tuple[int, int, int, int]]:
    async with session_maker() as session:
        result = await session.execute(
            text("SELECT id, elo, wins, losses, ties FROM battle_contenders")
        )
        return {
            str(r["id"]): (r["elo"], r["wins"], r["losses"], r["ties"])
            for r in result.mappings()
        }


async def _contender_ids(session_maker) -> list[str]:
    async with session_maker() as session:
        result = await session.execute(
            text("SELECT id FROM battle_contenders ORDER BY created_at, id")
        )
        return [str(r["id"]) for r in result.mappings()]


async def _completed_battle(
    session, sides: dict, winner: str | None, *, stop_reason=None, minutes=1
) -> None:
    """One settled battle, with ``finalized_at`` placed to fix the replay order."""
    columns = ", ".join(sides)
    values = ", ".join(f"CAST(:{key} AS UUID)" for key in sides)
    await session.execute(
        text(
            f"""
            INSERT INTO battles ({columns}, status, winner, judging_stop_reason,
                                 challenge_expires_at, agent_b_accepted_at,
                                 queued_at, started_at, deadline_at, is_rated,
                                 finalized_at, ended_at, task_id,
                                 task_title_snapshot, task_prompt_snapshot,
                                 task_rubric_snapshot, time_limit_seconds_snapshot)
            SELECT {values}, 'completed', :winner, :stop,
                   NOW(), NOW(), NOW(), NOW(), NOW() + INTERVAL '5 minutes', FALSE,
                   NOW() + make_interval(mins => :minutes), NOW(),
                   t.id, t.title, t.prompt, t.rubric, t.time_limit_seconds
              FROM battle_tasks t
             LIMIT 1
            """
        ),
        {**sides, "winner": winner, "stop": stop_reason, "minutes": minutes},
    )


def _cc(a: str, b: str) -> dict:
    """The two contender sides of a battle, named as the columns are."""
    return {"contender_a_id": a, "contender_b_id": b}


async def _agent(session) -> str:
    owner = await session.execute(
        text("INSERT INTO users (email) VALUES (:e) RETURNING id"),
        {"e": f"{uuid.uuid4()}@example.test"},
    )
    result = await session.execute(
        text(
            "INSERT INTO agents (handle, name, owner_user_id) "
            "VALUES (:h, :h, :o) RETURNING id"
        ),
        {"h": f"a-{uuid.uuid4().hex[:8]}", "o": owner.scalar_one()},
    )
    return str(result.scalar_one())


class TestRepositoryPrimitives:
    async def test_list_decided_battles_is_settlement_ordered_and_drops_the_rest(
        self, session_maker, clean_slate
    ) -> None:
        """The replay is order-sensitive (Elo is path-dependent), so the query
        must return settlement order — and only battles that decided something.

        MUTATION: drop the ``judging_stop_reason IS NULL`` clause, or the
        ``ORDER BY``. The length or the order assertion below goes red.
        """
        c0, c1, c2 = (await _contender_ids(session_maker))[:3]
        async with session_maker() as session:
            await _completed_battle(session, _cc(c1, c2), "b", minutes=9)
            await _completed_battle(session, _cc(c0, c1), "a", minutes=3)
            await _completed_battle(session, _cc(c0, c2), "tie", minutes=6)
            # None of the three below is a result about a contender.
            await _completed_battle(session, _cc(c0, c1), None)
            await _completed_battle(
                session, _cc(c0, c1), "a", stop_reason="global_budget_exhausted"
            )
            agent_id = await _agent(session)
            await _completed_battle(session, {"agent_a_id": agent_id, "contender_b_id": c2}, "a")
            await session.commit()

            rows = await BattleRepository(session).list_decided_contender_battles()

        assert [(str(r["contender_a_id"]), r["winner"]) for r in rows] == [
            (c0, "a"),
            (c0, "tie"),
            (c1, "b"),
        ]

    async def test_reset_returns_every_contender_to_the_column_defaults(
        self, session_maker, clean_slate
    ) -> None:
        """The reset is what makes a second run produce the first run's numbers.

        MUTATION: reset ``elo`` only. The counters stay dirty and this goes red.
        """
        ids = await _contender_ids(session_maker)
        async with session_maker() as session:
            repo = BattleRepository(session)
            for contender_id in ids[:2]:
                await repo.set_contender_rating(contender_id, 1350, 7, 2, 1)
            await session.commit()
        assert await _snapshot(session_maker) != {i: DEFAULT_RECORD for i in ids}

        async with session_maker() as session:
            await BattleRepository(session).lock_and_reset_contender_ratings()
            await session.commit()

        assert await _snapshot(session_maker) == {i: DEFAULT_RECORD for i in ids}

    async def test_set_rating_assigns_absolutely_and_reports_a_missing_row(
        self, session_maker, clean_slate
    ) -> None:
        """A replay computes totals, so the write must ASSIGN them — an
        increment would double-count a contender that already had a rating.

        MUTATION: make the UPDATE add instead of assign (``wins = wins + :wins``).
        The second assignment below lands on 9 and this goes red.
        """
        contender_id = (await _contender_ids(session_maker))[0]
        async with session_maker() as session:
            repo = BattleRepository(session)
            assert await repo.set_contender_rating(contender_id, 1400, 5, 1, 0) is True
            assert await repo.set_contender_rating(contender_id, 1250, 4, 3, 2) is True
            assert (
                await repo.set_contender_rating(str(uuid.uuid4()), 1400, 1, 0, 0)
            ) is False, "a missing contender is reported, not silently skipped"
            await session.commit()

        assert (await _snapshot(session_maker))[contender_id] == (1250, 4, 3, 2)


class TestBackfill:
    @pytest_asyncio.fixture(loop_scope="module")
    async def history(self, session_maker, clean_slate, monkeypatch):
        """Three decided battles plus one stopped one, and a live session maker."""
        monkeypatch.setattr(script, "async_session_maker", session_maker)
        c0, c1, c2 = (await _contender_ids(session_maker))[:3]
        async with session_maker() as session:
            await _completed_battle(session, _cc(c0, c1), "a", minutes=1)
            await _completed_battle(session, _cc(c0, c1), "a", minutes=2)
            await _completed_battle(session, _cc(c1, c2), "tie", minutes=3)
            await _completed_battle(
                session, _cc(c0, c2), "b", stop_reason="battle_attempt_cap", minutes=4
            )
            await session.commit()
        return c0, c1, c2

    async def test_a_replay_seeds_the_ratings_the_history_justifies(
        self, session_maker, history
    ) -> None:
        """End to end: the stored ratings must be the replay's ratings, the
        counters must count only decided battles, and the ladder must stay
        zero-sum against a roster that opened at the default.

        MUTATION: replay in reverse order, or count the stopped battle. The
        counters or the zero-sum assertion goes red.
        """
        c0, c1, c2 = history
        records = await script.backfill(dry_run=False)
        stored = await _snapshot(session_maker)

        assert {c: (r.elo, r.wins, r.losses, r.ties) for c, r in records.items()} == {
            c: stored[c] for c in records
        }, "the reported numbers are the persisted ones"
        assert stored[c0][1:] == (2, 0, 0)
        assert stored[c1][1:] == (0, 2, 1)
        assert stored[c2][1:] == (0, 0, 1)
        assert stored[c0][0] > DEFAULT_ELO > stored[c1][0]
        assert sum(r[0] for r in stored.values()) == DEFAULT_ELO * len(stored), (
            "rating is transferred, never minted"
        )
        untouched = set(stored) - {c0, c1, c2}
        assert all(stored[c] == DEFAULT_RECORD for c in untouched)

        assert await script.backfill(dry_run=False) is not None
        assert await _snapshot(session_maker) == stored, "a second run is a no-op"

    async def test_a_dry_run_leaves_the_stored_ratings_byte_identical(
        self, session_maker, history
    ) -> None:
        """The rehearsal must compute the same numbers and write none of them.

        MUTATION: commit before the ``dry_run`` branch. The equality goes red.
        """
        await script.backfill(dry_run=False)
        before = await _snapshot(session_maker)

        records = await script.backfill(dry_run=True)

        assert records, "the dry run still reports what it would have written"
        assert await _snapshot(session_maker) == before

    async def test_an_exception_mid_replay_leaves_the_table_at_its_pre_run_state(
        self, session_maker, history, monkeypatch
    ) -> None:
        """The reset precedes the replay, so a crash between them must not leave
        the roster wiped. The single transaction is what guarantees it: the
        table must be at its PRE-RUN ratings, not at the defaults.

        MUTATION: commit inside lock_and_reset_contender_ratings. The reset
        survives the rollback, the roster reads as defaults, and this goes red.
        """
        await script.backfill(dry_run=False)
        before = await _snapshot(session_maker)
        assert before != {c: DEFAULT_RECORD for c in before}, "a real pre-run state"

        original = BattleRepository.set_contender_rating
        calls: list[str] = []

        async def failing(self, contender_id, *args):
            calls.append(contender_id)
            if len(calls) == 2:
                raise RuntimeError("interrupted mid-replay")
            return await original(self, contender_id, *args)

        monkeypatch.setattr(BattleRepository, "set_contender_rating", failing)
        with pytest.raises(RuntimeError, match="interrupted mid-replay"):
            await script.backfill(dry_run=False)

        assert len(calls) == 2, "the crash landed after a write, not before one"
        assert await _snapshot(session_maker) == before
