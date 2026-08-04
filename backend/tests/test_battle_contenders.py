"""Tests for contender auto-battles (V72) — a continuous stream of battles
between DIFFERENT MODELS running DIFFERENT APPROACHES, with no human involved.

Integration by necessity, like every other battle test here: the properties
under test are arbitrated by real SQL against the real V66-V72 migrations on
testcontainers Postgres — the exactly-one-fighter-per-side CHECK, the seeded
rows, and the whole queued -> running -> judging -> completed drive. The
provider is mocked at ``battle_judges.call_judge_model``, which is the one call
path both contender answers and the judge panel go through.

What each test falsifies is stated on it.
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

from app.core.background import BattleMatchmakerTask
from app.core.config import get_settings
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import Side
from app.services.battle_judges import REPLICATE_COUNT, JudgeTransportError
from app.services.battle_runner import (
    _await_demo_drives,
    build_answer_messages,
    reconcile_once,
)
from app.services.battle_service import BattleMatchmaker
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
    "V72__battle_contenders.sql",
]

VALID_JUDGE_REPLY = (
    '{"vote": "submission_alpha", "confidence": 0.9, "reasoning": "ok", '
    '"scores": {"correctness": 1.0, "completeness": 1.0, "clarity": 1.0}}'
)

# A contender's answer is PROSE, and the provider mock must return prose for the
# answer calls: replying with the judge JSON above made both submissions look like
# a forced verdict to the injection detector, which quarantined the battle and
# settled it with no winner — green on `completed` + unrated, and wrong.
PLAIN_ANSWER = (
    "Use a token bucket per API key held in a shared store, refilled at the "
    "allowed rate, and fail open for a few seconds if the store is unreachable."
)


def _provider_mock() -> AsyncMock:
    """One provider stub for both call kinds, told apart by the payload.

    The judge payload names the two submissions `submission_alpha`/`beta`; an
    answer call never does. Discriminating on that is what keeps the mock honest
    for both paths in a single patch.
    """

    async def reply(**kwargs) -> str:
        user = kwargs["messages"][-1]["content"]
        return VALID_JUDGE_REPLY if "submission_alpha" in user else PLAIN_ANSWER

    return AsyncMock(side_effect=reply)

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


@contextmanager
def _no_transport():
    queued = AsyncMock(return_value=DeliveryResult.QUEUED)
    with (
        patch("app.services.battle_service.dispatch_existing", queued),
        patch("app.services.battle_runner.dispatch_existing", queued),
    ):
        yield


async def _contender_ids(session) -> list[str]:
    rows = await BattleRepository(session).list_enabled_contenders()
    return [str(r["id"]) for r in rows]


# ---------------------------------------------------------------------------
# The seed: a contender is (model, approach), and both directions exist.
# ---------------------------------------------------------------------------


class TestContenderSeed:
    async def test_seed_pairs_models_with_approaches_in_both_directions(
        self, db_session
    ) -> None:
        """The seeded roster must show the same model under two approaches AND
        the same approach under two models — the whole reason the table stores a
        pair rather than a model id.

        MUTATION: collapse the seed to one row per model. The one-model-two-
        approaches assertion goes red.
        """
        rows = await BattleRepository(db_session).list_enabled_contenders()
        assert len(rows) >= 5, "at least the five live-verified models"

        by_model: dict[str, set[str]] = {}
        by_approach: dict[str, set[str]] = {}
        for row in rows:
            by_model.setdefault(row["model_id"], set()).add(row["approach_key"])
            by_approach.setdefault(row["approach_key"], set()).add(row["model_id"])

        assert any(len(v) >= 2 for v in by_model.values()), "one model, two approaches"
        assert any(len(v) >= 2 for v in by_approach.values()), "one approach, two models"
        assert len(by_approach) >= 3, "at least three distinct approaches"

    async def test_seed_excludes_the_unusable_providers(self, db_session) -> None:
        """glm-4.7-flash times out, and groq/nebius/cerebras answer 403 or plain
        HTML on this server, so none of them may be fielded."""
        rows = await BattleRepository(db_session).list_enabled_contenders()
        assert all(r["model_id"] != "glm-4.7-flash" for r in rows)
        assert all(
            r["provider"] not in {"groq", "nebius", "cerebras"} for r in rows
        )

    async def test_seeded_task_pool_is_not_empty(self, db_session) -> None:
        """The matchmaker must have work on its FIRST tick, not after an operator
        remembers to generate tasks."""
        count = await db_session.execute(
            text(
                "SELECT COUNT(*) FROM battle_tasks "
                "WHERE status = 'ready' AND source = 'generated'"
            )
        )
        assert int(count.scalar_one()) >= 20

    async def test_each_contender_carries_its_own_approach_prompt(
        self, db_session
    ) -> None:
        """The approach IS a system prompt, and it is what the answer call sends.

        MUTATION: make ``_answer_with_model`` ignore ``system_prompt`` and use the
        demo framing instead. ``build_answer_messages`` then stops carrying the
        contender's text and this assertion goes red.
        """
        repo = BattleRepository(db_session)
        contender = await repo.get_contender((await _contender_ids(db_session))[0])
        messages = build_answer_messages(
            str(contender["system_prompt"]), "the task", [{"key": "correctness"}]
        )
        assert messages[0] == {
            "role": "system",
            "content": contender["system_prompt"],
        }
        assert "the task" in messages[1]["content"]


# ---------------------------------------------------------------------------
# The schema: exactly one fighter per side.
# ---------------------------------------------------------------------------


class TestSideExclusivity:
    async def test_a_side_may_not_hold_both_an_agent_and_a_contender(
        self, session_maker, db_session
    ) -> None:
        """MUTATION: drop battle_side_a_exactly_one_fighter — the insert below
        succeeds and a battle carries two fighters on one side."""
        contenders = await _contender_ids(db_session)
        async with session_maker() as session:
            uid = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO users (id, email) VALUES (CAST(:i AS UUID), :e)"
                ),
                {"i": uid, "e": f"o-{uid[:8]}@example.test"},
            )
            aid = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO agents (id, handle, owner_user_id) "
                    "VALUES (CAST(:i AS UUID), :h, CAST(:o AS UUID))"
                ),
                {"i": aid, "h": f"f-{aid[:8]}", "o": uid},
            )
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO battles (agent_a_id, contender_a_id, "
                        "agent_a_owner_snapshot, challenge_expires_at) "
                        "VALUES (CAST(:a AS UUID), CAST(:c AS UUID), "
                        "CAST(:o AS UUID), NOW() + INTERVAL '1 hour')"
                    ),
                    {"a": aid, "c": contenders[0], "o": uid},
                )
            await session.rollback()

    async def test_a_side_may_not_be_empty(self, session_maker) -> None:
        """A battle with neither an agent nor a contender on side A has no
        challenger at all."""
        async with session_maker() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO battles (challenge_expires_at) "
                        "VALUES (NOW() + INTERVAL '1 hour')"
                    )
                )
            await session.rollback()

    async def test_one_contender_may_not_fight_itself(
        self, session_maker, db_session
    ) -> None:
        contenders = await _contender_ids(db_session)
        async with session_maker() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text(
                        "INSERT INTO battles (contender_a_id, contender_b_id, "
                        "challenge_expires_at) VALUES (CAST(:c AS UUID), "
                        "CAST(:c AS UUID), NOW() + INTERVAL '1 hour')"
                    ),
                    {"c": contenders[0]},
                )
            await session.rollback()


# ---------------------------------------------------------------------------
# The matchmaker: one battle per tick, capped, never starving.
# ---------------------------------------------------------------------------


class TestMatchmaker:
    async def test_tick_creates_a_bound_queued_battle_between_two_contenders(
        self, session_maker
    ) -> None:
        """One tick produces a battle that is ready to run: two DIFFERENT
        contenders, a bound task, status 'queued', and unrated.

        MUTATION: have create_contender_battle insert 'challenge_pending' — the
        status assertion goes red, and nothing would ever drive the battle (the
        accepted/reserved phases need agents).
        """
        battle_id = await BattleMatchmaker(session_maker).tick()
        assert battle_id is not None
        async with session_maker() as session:
            row = await BattleRepository(session).get(battle_id)
        assert row["status"] == "queued"
        assert row["contender_a_id"] != row["contender_b_id"]
        assert row["agent_a_id"] is None and row["agent_b_id"] is None
        assert row["task_id"] is not None and row["task_prompt_snapshot"]
        assert row["rated_eligible"] is False
        assert row["rated_ineligibility_reason"] == "auto"

    async def test_tick_respects_the_concurrency_cap(self, session_maker) -> None:
        """The cap is the budget: z.ai holds balance only on the free flash tier
        and tops out near three in-flight requests, so an uncapped matchmaker
        wedges the page on 429s.

        MUTATION: delete the count_active_contender_battles guard in tick(). The
        second tick then creates a battle and this goes red.
        """
        matchmaker = BattleMatchmaker(session_maker)
        matchmaker.settings.battle_auto_max_running = 1
        try:
            async with session_maker() as session:
                live = await BattleRepository(session).count_active_contender_battles()
            assert live >= 1, "the previous test left one live auto-battle"
            assert await matchmaker.tick() is None
        finally:
            matchmaker.settings.battle_auto_max_running = 2

    async def test_two_draws_take_different_tasks(self, session_maker) -> None:
        """pick_auto_task claims in the same statement it reads, so two ticks
        cannot bind the same task and run the identical battle twice."""
        async with session_maker() as session:
            repo = BattleRepository(session)
            first = await repo.pick_auto_task()
            second = await repo.pick_auto_task()
            await session.commit()
        assert first is not None and second is not None
        assert first["id"] != second["id"]


# ---------------------------------------------------------------------------
# The drive: both sides answered by their own model, judged, settled unrated.
# ---------------------------------------------------------------------------


class TestAutoBattleDrive:
    async def test_reconciler_runs_an_auto_battle_to_a_verdict(
        self, session_maker
    ) -> None:
        """End to end with a mocked provider: queued -> running, BOTH contender
        answers submitted, then judged and completed as unrated.

        MUTATION: remove the contender branch in reconcile_once's queued phase.
        start_queued then fails on the missing agents and the battle never leaves
        'queued' — every assertion below goes red.
        """
        battle_id = await BattleMatchmaker(session_maker).tick()
        assert battle_id is not None
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        answer = _provider_mock()

        with _no_transport(), patch("app.services.battle_runner.call_judge_model", answer):
            # >= 1, not == 1: earlier tests in this module left their own
            # auto-battles queued, and one pass starts every claimable row.
            counts = await drive()
            assert counts["started"] >= 1, counts
            await _await_demo_drives()

        async with session_maker() as session:
            repo = BattleRepository(session)
            row = await repo.get(battle_id)
            subs = await repo.list_submissions(battle_id)
        assert row["status"] == "running"
        finals = {str(s["side"]) for s in subs if s["is_final"]}
        assert finals == {Side.A.value, Side.B.value}, "both models answered"
        assert all(s["content"] for s in subs if s["is_final"])

        # Both sides are final, so close_deadline closes the battle early rather
        # than waiting out the wall clock. The running lease taken at the start
        # must be lapsed first — in production the next tick waits it out.
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET lease_token = NULL, lease_expires_at = NULL "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"b": battle_id},
            )
            await session.commit()

        with _no_transport(), patch("app.services.battle_runner.call_judge_model", answer):
            await drive()

        async with session_maker() as session:
            repo = BattleRepository(session)
            row = await repo.get(battle_id)
            judgements = await repo.list_judgements(battle_id)
        assert row["status"] == "completed", "reached a judged result"
        assert row["is_rated"] is False, "an auto-battle never rates"
        # 'completed' + is_rated False is ALSO what a fully-failed panel produces,
        # so those two assertions alone cannot see a judge path that died. These
        # can: the panel has to have actually voted. Without the NULL-owner fix in
        # battle_budget.reserve_call, reserve_call raises a DataError on
        # CAST('None' AS UUID), every half returns an error vote, and all three go
        # red — no judgement rows, no winner, and a judging_stop_reason.
        assert len(judgements) == REPLICATE_COUNT, "every replicate voted"
        assert row["winner"] in {"a", "b", "tie"}, "the panel reached a verdict"
        assert row["judging_stop_reason"] is None, "judging ran to completion"

    async def test_a_side_answers_with_its_own_model_and_prompt(
        self, session_maker
    ) -> None:
        """The call each side makes is described by ITS contender row — the wire
        model and the system prompt both come out of it. Two contenders on one
        task is only a comparison if this holds.

        MUTATION: hardcode DEMO_ANSWER_MODEL in drive_contender_submission. The
        wire-model assertion goes red.
        """
        battle_id = await BattleMatchmaker(session_maker).tick()
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        answer = _provider_mock()

        with _no_transport(), patch("app.services.battle_runner.call_judge_model", answer):
            await drive()
            await _await_demo_drives()

        async with session_maker() as session:
            repo = BattleRepository(session)
            row = await repo.get(battle_id)
            sides = [
                await repo.get_contender(str(row[f"contender_{side}_id"]))
                for side in ("a", "b")
            ]
        sent = [call.kwargs for call in answer.await_args_list]
        systems = {c["messages"][0]["content"] for c in sent}
        models = " ".join(c["wire_model"] for c in sent)
        for contender in sides:
            assert contender["system_prompt"] in systems
            assert contender["model_id"] in models


class TestOperatorSwitch:
    async def test_the_stream_is_off_until_an_operator_turns_it_on(self) -> None:
        """Merging must not start spending provider calls anywhere by itself."""
        assert get_settings().battle_auto_enabled is False

    async def test_the_cadence_is_read_live_like_the_kill_switch(self) -> None:
        """Both halves of the switch must behave the same way.

        MUTATION: turn interval_s back into a class attribute assigned at
        definition time. It then ignores the changed setting until a restart,
        while battle_auto_enabled beside it takes effect immediately — and this
        assertion goes red.
        """
        settings = get_settings()
        original = settings.battle_auto_interval_seconds
        try:
            settings.battle_auto_interval_seconds = 123
            assert BattleMatchmakerTask().interval_s == 123
        finally:
            settings.battle_auto_interval_seconds = original


# ---------------------------------------------------------------------------
# A side that never answered forfeits — it must never reach the panel.
# ---------------------------------------------------------------------------


async def _fresh_auto_battle(session_maker) -> str:
    """One new auto-battle, ignoring the concurrency cap.

    Earlier tests in this module leave their battles live, so the default cap of
    two would refuse the tick — and the cap is not what these tests measure (it
    has its own test above).
    """
    matchmaker = BattleMatchmaker(session_maker)
    original = matchmaker.settings.battle_auto_max_running
    matchmaker.settings.battle_auto_max_running = 999
    try:
        battle_id = await matchmaker.tick()
    finally:
        matchmaker.settings.battle_auto_max_running = original
    assert battle_id is not None
    return battle_id


async def _drive_to_terminal(drive, session_maker, battle_id: str, answer) -> dict:
    """Run reconcile passes until this battle is terminal. Returns its row.

    One pass takes ONE step per battle, and the module's earlier tests leave
    their own battles live in the same claim batch, so a fixed number of passes
    would be a race rather than a wait. Bounded, so a battle that genuinely
    cannot settle fails the assertion instead of hanging.
    """
    for _ in range(8):
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", answer):
            await drive()
            await _await_demo_drives()
        async with session_maker() as session:
            row = await BattleRepository(session).get(battle_id)
        if row["status"] == "completed":
            return row
        await _expire_deadline(session_maker, battle_id)
    return row


def _mock_with_failing_prompt(failing_prompts: set[str]) -> AsyncMock:
    """Provider stub where the named system prompts fail the way Mistral did.

    A 422 on the request body reaches the runner as JudgeTransportError, so that
    is what the side whose contender is broken raises. Every other call answers
    normally, and judge calls (identified by the payload's opaque submission
    labels) answer with a verdict — which is how the test can prove the panel
    was never called at all.
    """

    async def reply(**kwargs) -> str:
        messages = kwargs["messages"]
        if "submission_alpha" in messages[-1]["content"]:
            return VALID_JUDGE_REPLY
        if messages[0]["content"] in failing_prompts:
            raise JudgeTransportError("HTTP 422: extra_forbidden on body.seed")
        return PLAIN_ANSWER

    return AsyncMock(side_effect=reply)


async def _contender_prompts(session_maker, battle_id: str) -> dict[str, str]:
    async with session_maker() as session:
        repo = BattleRepository(session)
        row = await repo.get(battle_id)
        return {
            side: str((await repo.get_contender(str(row[f"contender_{side}_id"])))["system_prompt"])
            for side in ("a", "b")
        }


async def _expire_deadline(session_maker, battle_id: str) -> None:
    """Free the running lease and pull the deadline into the past.

    In production the deadline simply arrives; a back-to-back test pass has to
    say so explicitly. started_at + 1ms keeps the monotonic-timeline CHECKs true.
    """
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE battles SET lease_token = NULL, lease_expires_at = NULL, "
                "deadline_at = started_at + INTERVAL '1 millisecond' "
                "WHERE id = CAST(:b AS UUID)"
            ),
            {"b": battle_id},
        )
        await session.commit()


class TestSilentForfeit:
    async def test_a_side_that_never_answered_loses_and_the_panel_never_runs(
        self, session_maker
    ) -> None:
        """The production defect: three contenders whose provider rejected every
        request submitted nothing, and the judges scored that emptiness against a
        real answer as a TIE — 13 of 24 battles, three contenders on zero wins.

        MUTATION: delete the silent_sides short-circuit in _judge_and_settle. The
        panel then runs on an empty submission, judgement rows appear and the
        winner is decided by a model reading nothing — all three assertions go red.
        """
        battle_id = await _fresh_auto_battle(session_maker)
        prompts = await _contender_prompts(session_maker, battle_id)
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        answer = _mock_with_failing_prompt({prompts["b"]})

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert row["status"] == "completed"
        assert row["winner"] == "a", "the side that answered wins by forfeit"
        assert judgements == [], "no judge ever scored the empty submission"
        assert "forfeit" in row["verdict_reason"]

    async def test_neither_side_answering_is_no_contest(self, session_maker) -> None:
        """Both silent is not a tie either — a tie is a substantive verdict, and
        nobody judged anything. Winner NULL, and still no judge call spent."""
        battle_id = await _fresh_auto_battle(session_maker)
        prompts = await _contender_prompts(session_maker, battle_id)
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        answer = _mock_with_failing_prompt({prompts["a"], prompts["b"]})

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert row["status"] == "completed"
        assert row["winner"] is None
        assert judgements == []
        assert "no contest" in row["verdict_reason"]
