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

import asyncio
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
from app.services.battle_judges import (
    RECUSED_JUDGE_REF,
    REPLICATE_COUNT,
    JudgeModel,
    JudgeTransportError,
    replicate_seed,
)
from app.services.battle_runner import (
    PROVIDER_UNREACHABLE_ERROR,
    BattleRunner,
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
    "V73__contender_rating.sql",
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


@pytest.fixture(autouse=True)
def _impartial_judge_roster():
    """Seat a judge model that fights in no battle, for the whole module.

    Without it the panel's roster is whatever the environment resolves — in a
    test environment only the primary, kimi-k3, which is ALSO a seeded contender.
    Every draw that happened to field kimi would then be fully recused and settle
    with no verdict, making these drive tests depend on the matchmaker's dice.
    TestJudgeRecusal patches over this with a roster of its own.
    """
    with patch.object(
        BattleRunner, "_resolve_judge_roster", return_value=_roster(["panel/impartial"])
    ):
        yield


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


def _mock_with_failing_side(
    failing: set[tuple[str, str]], *, error: Exception | None = None
) -> AsyncMock:
    """Provider stub where the named contenders produce no usable answer.

    A contender is identified by the PAIR (wire model, system prompt), which is
    what makes it unique — two contenders routinely share one approach prompt
    (three seeded rows use `stepwise`) or one model (the same model under two
    approaches), so either field alone silences both sides and the test then
    measures the wrong outcome.

    Two DIFFERENT failures, because they settle differently. By default the model
    answers with an empty string — it was asked and produced nothing, which
    forfeits. With ``error`` the call raises instead: the provider was never
    reached, which voids.

    Judge calls (identified by the payload's opaque submission labels) always
    answer with a verdict, so a test can prove the panel was never called.
    """

    async def reply(**kwargs) -> str:
        messages = kwargs["messages"]
        if "submission_alpha" in messages[-1]["content"]:
            return VALID_JUDGE_REPLY
        if (kwargs["wire_model"], messages[0]["content"]) in failing:
            if error is not None:
                raise error
            return ""
        return PLAIN_ANSWER

    return AsyncMock(side_effect=reply)


async def _contender_ident(session_maker, battle_id: str) -> dict[str, tuple[str, str]]:
    """Each side's (wire model, system prompt) — the pair that names a contender."""
    async with session_maker() as session:
        repo = BattleRepository(session)
        row = await repo.get(battle_id)
        out = {}
        for side in ("a", "b"):
            c = await repo.get_contender(str(row[f"contender_{side}_id"]))
            out[side] = (str(c["model_id"]), str(c["system_prompt"]))
        return out


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
        sides = await _contender_ident(session_maker, battle_id)
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        answer = _mock_with_failing_side({sides["b"]})

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
        sides = await _contender_ident(session_maker, battle_id)
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        answer = _mock_with_failing_side({sides["a"], sides["b"]})

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert row["status"] == "completed"
        assert row["winner"] is None
        assert judgements == []
        assert "no contest" in row["verdict_reason"]


class TestUnreachableProviderVoids:
    async def test_an_upstream_5xx_voids_the_battle_instead_of_forfeiting(
        self, session_maker
    ) -> None:
        """A provider outage must not hand the opponent a win.

        Production: `HTTP 500 {"object":"error","message":"Service unavailable."}`
        from mistral, and the battle was recorded as a forfeit — a loss for a
        model that was never asked. Void is the only honest outcome: the side
        that DID answer keeps no win, because it out-answered nobody.

        MUTATION: drop the `unreachable` branch in settle_silent_forfeit. The
        winner becomes 'a' and this goes red.
        """
        battle_id = await _fresh_auto_battle(session_maker)
        sides = await _contender_ident(session_maker, battle_id)
        drive = partial(
            reconcile_once,
            session_factory=session_maker,
            gate=None,
            provider={"api_key": "k", "base_url": "http://unused"},
        )
        answer = _mock_with_failing_side(
            {sides["b"]},
            error=JudgeTransportError('HTTP 500: {"message":"Service unavailable."}'),
        )

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert row["status"] == "completed"
        assert row["winner"] is None, "nobody wins a battle the provider never served"
        assert row["is_rated"] is False
        assert judgements == [], "no judge call spent on a battle that cannot be judged"
        assert "void" in row["verdict_reason"]

    async def test_gate_saturation_voids_too_and_is_not_retried(
        self, session_maker
    ) -> None:
        """A refused gate slot means the model was never asked either.

        It is also the one transient failure the answer path must NOT retry: the
        bounded wait was already spent against a busy account, so a second
        attempt just spends another. Exactly one call reaches this side.
        """
        battle_id = await _fresh_auto_battle(session_maker)
        sides = await _contender_ident(session_maker, battle_id)
        drive = partial(
            reconcile_once,
            session_factory=session_maker,
            gate=None,
            provider={"api_key": "k", "base_url": "http://unused"},
        )
        answer = _mock_with_failing_side(
            {sides["a"]},
            error=JudgeTransportError(
                "gate saturated: no slot on llm_gate:mistral:platform within 20.0s",
                saturated=True,
            ),
        )

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        assert row["status"] == "completed"
        assert row["winner"] is None
        assert "void" in row["verdict_reason"]
        attempts = [
            c
            for c in answer.await_args_list
            if (c.kwargs["wire_model"], c.kwargs["messages"][0]["content"]) == sides["a"]
        ]
        assert len(attempts) == 1, "a saturated gate is not retried"

    async def test_a_transient_error_is_retried_once_and_can_still_answer(
        self, session_maker
    ) -> None:
        """One retry rescues an upstream blip, and the battle is judged normally.

        MUTATION: set _ANSWER_ATTEMPTS to 1. The first failure becomes final, the
        battle voids, and both assertions go red.
        """
        battle_id = await _fresh_auto_battle(session_maker)
        sides = await _contender_ident(session_maker, battle_id)
        seen: list[tuple[str, str]] = []

        async def flaky(**kwargs) -> str:
            messages = kwargs["messages"]
            if "submission_alpha" in messages[-1]["content"]:
                return VALID_JUDGE_REPLY
            ident = (kwargs["wire_model"], messages[0]["content"])
            seen.append(ident)
            if ident == sides["b"] and seen.count(ident) == 1:
                raise JudgeTransportError("HTTP 503: upstream connect error")
            return PLAIN_ANSWER

        answer = AsyncMock(side_effect=flaky)
        drive = partial(
            reconcile_once,
            session_factory=session_maker,
            gate=None,
            provider={"api_key": "k", "base_url": "http://unused"},
        )

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        assert seen.count(sides["b"]) == 2, "failed once, retried once"
        assert row["status"] == "completed"
        assert row["winner"] in {"a", "b", "tie"}, "a judged result, not a void"


class TestDriveLevelUnreachable:
    """The layer the other tests skip.

    Every void test above injects its failure at the call_judge_model boundary,
    so none of them exercises the drive's own failure paths — which is where the
    void mechanism fails OPEN to a forfeit, and where the slow-unreachable defect
    lived.
    """

    async def test_a_provider_that_hangs_past_the_drive_budget_voids(
        self, session_maker
    ) -> None:
        """A hung provider must be recorded by the DRIVE, not left to the deadline.

        MUTATION: drop the _record_drive_unreachable call from the TimeoutError
        branch. The row never appears, close_deadline writes silent-fighter, and
        the battle forfeits — the exact defect, restored.
        """
        battle_id = await _fresh_auto_battle(session_maker)
        sides = await _contender_ident(session_maker, battle_id)
        drive = partial(
            reconcile_once,
            session_factory=session_maker,
            gate=None,
            provider={"api_key": "k", "base_url": "http://unused"},
        )

        async def hangs(**kwargs) -> str:
            if "submission_alpha" in kwargs["messages"][-1]["content"]:
                return VALID_JUDGE_REPLY
            if (kwargs["wire_model"], kwargs["messages"][0]["content"]) == sides["b"]:
                await asyncio.sleep(30)
            return PLAIN_ANSWER

        answer = AsyncMock(side_effect=hangs)
        with (
            patch("app.services.battle_runner.ANSWER_DRIVE_BUDGET_SECONDS", 0.05),
            _no_transport(),
            patch("app.services.battle_runner.call_judge_model", answer),
        ):
            await drive()
            await _await_demo_drives()

        async with session_maker() as session:
            subs = await BattleRepository(session).list_submissions(battle_id)
        empty_b = [
            s for s in subs if str(s["side"]) == Side.B.value and s["is_final"]
        ]
        assert len(empty_b) == 1, "the drive recorded the hung side"
        assert empty_b[0]["error"].startswith(PROVIDER_UNREACHABLE_ERROR)

        row = await _drive_to_terminal(drive, session_maker, battle_id, answer)
        assert row["winner"] is None, "a hung provider voids, never forfeits"
        assert "void" in row["verdict_reason"]

    async def test_a_refused_insert_is_logged_and_not_reported_as_void(
        self, session_maker
    ) -> None:
        """The race: close_deadline already wrote this side's final row.

        add_submission then refuses ours, the void marker is LOST, and the battle
        settles as a forfeit. That outcome is not what we want but it IS what
        happens, so the only defensible behaviour is to say so — the defect was a
        log line asserting "battle will be void" regardless.

        MUTATION: discard the accepted flag again. record_unreachable returns
        None, the first assertion goes red.
        """
        battle_id = await _fresh_auto_battle(session_maker)
        drive = partial(
            reconcile_once,
            session_factory=session_maker,
            gate=None,
            provider={"api_key": "k", "base_url": "http://unused"},
        )
        answer = _mock_with_failing_side(set())
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", answer):
            await drive()
            await _await_demo_drives()

        # Side B already carries a final answer, so the slot is taken — the same
        # state close_deadline leaves behind when it wins the race.
        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            accepted = await runner.record_unreachable(
                battle_id, Side.B, "provider error or timeout"
            )
        assert accepted is False, "the refused insert is reported, not assumed"

        async with session_maker() as session:
            subs = await BattleRepository(session).list_submissions(battle_id)
        finals_b = [s for s in subs if str(s["side"]) == Side.B.value and s["is_final"]]
        assert len(finals_b) == 1, "no second final row was created"
        assert finals_b[0]["content"], "the real answer still stands"


# ---------------------------------------------------------------------------
# Recusal: a model may not judge a battle it is fighting.
# ---------------------------------------------------------------------------


def _roster(model_ids: list[str]) -> list[JudgeModel]:
    return [
        JudgeModel(
            model_id=mid,
            provider=mid.split("/", 1)[0],
            base_url="http://unused",
            api_key="k",
            wire_model=mid.split("/", 1)[-1],
        )
        for mid in model_ids
    ]


def _judge_calls_for(answer, task_prompt: str) -> list[dict]:
    """The judge calls made for ONE battle.

    Two discriminators, both needed: an answer call never names the opaque
    labels, and other battles driven by the same reconcile pass judge a
    different task.
    """
    return [
        call.kwargs
        for call in answer.await_args_list
        if "submission_alpha" in call.kwargs["messages"][-1]["content"]
        and task_prompt[:60] in call.kwargs["messages"][-1]["content"]
    ]


async def _contender_ratings(session_maker, contenders: list[dict]) -> dict:
    """(elo, wins, losses, ties) per contender id — the ladder, nothing else."""
    async with session_maker() as session:
        rows = await BattleRepository(session).list_contender_ratings()
    wanted = {str(c["id"]) for c in contenders}
    return {
        str(r["id"]): (r["elo"], r["wins"], r["losses"], r["ties"])
        for r in rows
        if str(r["id"]) in wanted
    }


async def _battle_and_fighters(session_maker) -> tuple[str, dict, list[dict]]:
    """A new auto-battle, its row and both contender rows."""
    battle_id = await _fresh_auto_battle(session_maker)
    async with session_maker() as session:
        repo = BattleRepository(session)
        row = await repo.get(battle_id)
        fighting = [
            await repo.get_contender(str(row[f"contender_{side}_id"]))
            for side in ("a", "b")
        ]
    return battle_id, row, fighting


class TestJudgeRecusal:
    async def test_a_fighting_model_never_judges_and_the_rest_reach_quorum(
        self, session_maker
    ) -> None:
        """The two contenders are seated on the roster alongside two other
        models. Neither contender may cast a vote, and the survivors must still
        fill all three replicates.

        MUTATION: drop the seatable_judges call in run_judge_panel — a contender
        appears as a replicate's judge_ref and the assertion goes red.
        """
        battle_id, row, fighting = await _battle_and_fighters(session_maker)
        fighting_ids = {f"{c['provider']}/{c['model_id']}" for c in fighting}
        roster = _roster([*fighting_ids, "mistral/mistral-medium-2508", "deepseek/x"])
        answer = _provider_mock()

        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        with patch.object(BattleRunner, "_resolve_judge_roster", return_value=roster):
            settled = await _drive_to_terminal(drive, session_maker, battle_id, answer)

        assert _judge_calls_for(answer, row["task_prompt_snapshot"]), "the panel ran"

        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert len(judgements) == REPLICATE_COUNT, "recusal still filled every replicate"
        assert {str(j["judge_ref"]) for j in judgements} & fighting_ids == set()
        assert settled["status"] == "completed"
        assert settled["winner"] in {"a", "b", "tie"}, "quorum was reached"

    async def test_votes_already_cast_by_a_barred_model_cannot_rate(
        self, session_maker
    ) -> None:
        """Three real votes are already persisted when recusal fires — a lease
        lost after the panel committed, then a later pass finds the roster
        conflicted. The freeze skips replicates that already carry a row, so
        those votes SURVIVE and reach quorum; the outcome must still be no
        result and an untouched ladder, because they were cast by the model that
        is now barred.

        MUTATION: drop `override_verdict=forced` from settle_panel_recused and
        let resolve_verdict read the persisted votes — winner becomes 'a' and the
        contender ladder moves.
        """
        battle_id, _row, fighting = await _battle_and_fighters(session_maker)
        before = await _contender_ratings(session_maker, fighting)
        answer = _provider_mock()
        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )

        # Walk it to 'judging' with both answers in, then plant the votes the
        # conflicted panel would have committed before its lease lapsed.
        with _no_transport(), patch("app.services.battle_runner.call_judge_model", answer):
            await drive()
            await _await_demo_drives()
        await _expire_deadline(session_maker, battle_id)
        async with session_maker() as session:
            repo = BattleRepository(session)
            for replicate_no in range(REPLICATE_COUNT):
                await repo.upsert_judgement(
                    battle_id=battle_id,
                    judge_kind="llm",
                    judge_ref=f"{fighting[0]['provider']}/{fighting[0]['model_id']}",
                    replicate_seed=replicate_seed(battle_id, replicate_no),
                    vote="a",
                    confidence=0.9,
                )
            token = str(uuid.uuid4())
            await session.execute(
                text(
                    "UPDATE battles SET status = 'judging', lease_token = :t, "
                    "lease_expires_at = NOW() + INTERVAL '5 minutes' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"t": token, "b": battle_id},
            )
            await session.commit()

        async with session_maker() as session:
            await BattleRunner(session, None).settle_panel_recused(battle_id, token)

        async with session_maker() as session:
            settled = await BattleRepository(session).get(battle_id)
        assert settled["status"] == "completed"
        assert settled["winner"] is None, "a self-judged verdict was published"
        # One collapsed vote per replicate, which upsert_judgement promises and
        # `len(judgements) >= REPLICATE_COUNT` relies on. The freeze writes a
        # judge_ref of its own, so ON CONFLICT DO NOTHING (keyed on judge_ref,
        # V66:486) does NOT suppress it over a real vote — only the explicit skip
        # of already-decided seeds does.
        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert len(judgements) == REPLICATE_COUNT, "the freeze duplicated a replicate"
        assert {str(j["judge_ref"]) for j in judgements} == {
            f"{fighting[0]['provider']}/{fighting[0]['model_id']}"
        }, "a real vote lost its attribution to the model that cast it"
        assert await _contender_ratings(session_maker, fighting) == before, (
            "votes from the barred model moved the ladder"
        )

    async def test_a_recused_settle_without_the_lease_writes_nothing(
        self, session_maker
    ) -> None:
        """A worker whose lease lapsed must not freeze error votes over the live
        panel of the worker that now owns the battle.

        MUTATION: remove the renew_battle_lease guard — the stale worker commits
        three terminal error rows and this goes red.
        """
        battle_id, _row, fighting = await _battle_and_fighters(session_maker)
        async with session_maker() as session:
            await session.execute(
                text(
                    "UPDATE battles SET status = 'judging', lease_token = :t, "
                    "lease_expires_at = NOW() + INTERVAL '5 minutes', "
                    "started_at = NOW(), deadline_at = NOW() + INTERVAL '1 minute' "
                    "WHERE id = CAST(:b AS UUID)"
                ),
                {"t": str(uuid.uuid4()), "b": battle_id},
            )
            await session.commit()

        async with session_maker() as session:
            outcome = await BattleRunner(session, None).settle_panel_recused(
                battle_id, str(uuid.uuid4())
            )
        assert outcome is None, "a stale worker settled someone else's battle"
        async with session_maker() as session:
            repo = BattleRepository(session)
            assert await repo.list_judgements(battle_id) == []
            assert (await repo.get(battle_id))["status"] == "judging"

    async def test_a_fully_recused_panel_completes_without_a_verdict(
        self, session_maker
    ) -> None:
        """When the roster holds nothing but the two fighters, the battle must
        land on the existing no-quorum path — not judge itself, and not complete
        as if it had been judged.

        MUTATION: fall back to the unfiltered roster when nothing is seatable —
        the no-judge-call assertion and winner=None both go red.
        """
        battle_id, row, fighting = await _battle_and_fighters(session_maker)
        roster = _roster([f"{c['provider']}/{c['model_id']}" for c in fighting])
        answer = _provider_mock()
        # Snapshot rather than compare against DEFAULT_ELO: earlier tests in this
        # module rate their own auto-battles, so these contenders may already
        # carry history. What must hold is that THIS battle adds none.
        before = await _contender_ratings(session_maker, fighting)

        provider = {"api_key": "k", "base_url": "http://unused"}
        drive = partial(
            reconcile_once, session_factory=session_maker, gate=None, provider=provider
        )
        with patch.object(BattleRunner, "_resolve_judge_roster", return_value=roster):
            settled = await _drive_to_terminal(drive, session_maker, battle_id, answer)

        assert _judge_calls_for(answer, row["task_prompt_snapshot"]) == [], (
            "a fighter was asked to judge its own battle"
        )

        async with session_maker() as session:
            judgements = await BattleRepository(session).list_judgements(battle_id)
        assert settled["status"] == "completed", "the battle is not stranded"
        assert settled["winner"] is None, "no verdict was invented"
        assert {str(j["vote"]) for j in judgements} == {"error"}
        # The freeze is attributed to the panel, never to the barred model:
        # judge_ref is public via GET /battles/{id}/judgements.
        assert {str(j["judge_ref"]) for j in judgements} == {RECUSED_JUDGE_REF}
        # The prefix is a CONTRACT, not prose: verdict_reason is the only public
        # field carrying this outcome and the UI branches on the token, exactly
        # as it branches on "void: ". Without it a recused battle renders as
        # "finished without quorum" — judges blamed for calls never made.
        assert (settled["verdict_reason"] or "").startswith("recused: ")
        assert "no judge model free of conflict" in settled["verdict_reason"]

        # `is_rated` is the AGENT flag and is False on EVERY auto-battle, so it
        # cannot see a contender ladder that moved. The rating columns can.
        assert await _contender_ratings(session_maker, fighting) == before, (
            "a recused battle moved the contender ladder"
        )
