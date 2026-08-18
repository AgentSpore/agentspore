"""Battle-runner-level tests for the agentic contender path (V75).

Integration by necessity: the property under test is arbitrated by real SQL
(the execution_mode branch, add_submission's monotonic seq_no, silent_sides'
empty-content read) against the real V66-V75 migrations on testcontainers
Postgres. run_agentic_answer itself is mocked here — its own behaviour (NDJSON
parsing, soft-deadline cutoff, cleanup) is covered by test_agentic_answer.py;
this file covers what a subagent-level unit test structurally cannot: whether
a timeout outcome reaches settlement as forfeit/void (never a graded verdict)
and whether TIMEOUT_PARTIAL_MARKER ever reaches the judge as answer text.

V75 flips every ENABLED seeded contender to execution_mode='agent' (its own
migration comment). Kept in its OWN engine/module here, separate from
test_battle_contenders.py and test_battle_runner.py, so this file's V75
inclusion cannot flip contenders those suites still exercise as 'model'.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from conftest import split_sql_statements
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.rating import DEFAULT_ELO
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import Side, TaskSource
from app.services.agentic_answer import AgentStep
from app.services.battle_runner import TIMEOUT_PARTIAL_MARKER, BattleRunner

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
    "V74__battle_judge_seat_once.sql",
    "V75__contender_execution_mode.sql",
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
    sql = BASE_SCHEMA + ";" + ";".join((MIGRATIONS / name).read_text() for name in _MIG_FILES)
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


async def _new_contender(
    session, *, execution_mode: str, model_id: str, system_prompt: str, provider: str = "zai"
) -> str:
    cid = str(uuid.uuid4())
    await session.execute(
        text(
            """
            INSERT INTO battle_contenders
                (id, provider, model_id, approach_key, system_prompt, display_name,
                 execution_mode, elo, enabled)
            VALUES
                (CAST(:id AS UUID), :provider, :model_id, 'plain', :system_prompt, :model_id,
                 :execution_mode, :elo, TRUE)
            """
        ),
        {
            "id": cid,
            "provider": provider,
            "model_id": model_id,
            "system_prompt": system_prompt,
            "execution_mode": execution_mode,
            "elo": DEFAULT_ELO,
        },
    )
    return cid


async def _running_battle(
    session_maker, *, agentic_side_b: bool, provider_b: str = "zai"
) -> tuple[str, str, str]:
    """A 'running' battle: side A a MODEL contender, side B either agent or model.

    Mixed by default (agentic_side_b picks B's mode, A always 'model') so the
    forfeit/timeout assertions are not entangled with the both-agentic
    path-judging branch, which is a separate concern. ``provider_b`` defaults
    to 'zai' (an EXTRA_PROVIDERS member) so existing forfeit/timeout tests are
    unaffected; TestAgenticContenderCredentials overrides it to exercise the
    OpenRouter-vs-unconfigured-extra-provider split.
    """
    async with session_maker() as session:
        repo = BattleRepository(session)
        task_id = await repo.create_task(
            source=TaskSource.GENERATED,
            category="general",
            title="Write a haiku",
            prompt="Write a haiku about databases.",
            rubric=[],
            time_limit_seconds=600,
            created_by_user_id=None,
        )
        unique = uuid.uuid4().hex[:8]
        contender_a = await _new_contender(
            session,
            execution_mode="model",
            model_id=f"glm-4.5-{unique}",
            system_prompt="answer plainly",
        )
        contender_b = await _new_contender(
            session,
            execution_mode="agent" if agentic_side_b else "model",
            model_id=f"glm-4.5-flash-{unique}",
            system_prompt="answer as an agent with tools",
            provider=provider_b,
        )
        battle_id = str(uuid.uuid4())
        # No agent_a/b_owner_snapshot: a contender-only battle has none (V72
        # battle_owner_snapshot_a_requires_agent) -- unlike an agent fighter.
        await session.execute(
            text(
                """
                INSERT INTO battles
                    (id, contender_a_id, contender_b_id,
                     status, task_id, task_title_snapshot,
                     task_prompt_snapshot, task_rubric_snapshot,
                     time_limit_seconds_snapshot,
                     agent_b_accepted_at, queued_at,
                     started_at, deadline_at, challenge_expires_at)
                VALUES
                    (CAST(:id AS UUID), CAST(:ca AS UUID), CAST(:cb AS UUID),
                     'running', CAST(:task_id AS UUID), 'Write a haiku',
                     'Write a haiku about databases.', CAST(:rubric AS JSONB),
                     600,
                     NOW(), NOW(),
                     NOW(), NOW() + INTERVAL '600 seconds', NOW() + INTERVAL '1 hour')
                """
            ),
            {
                "id": battle_id,
                "ca": contender_a,
                "cb": contender_b,
                "task_id": task_id,
                "rubric": "[]",
            },
        )
        await session.commit()
    return battle_id, contender_a, contender_b


class TestAgenticTimeoutIsAThirdOutcome:
    """A soft-deadline cutoff with NO drafted text must forfeit like an
    ordinary silent side — never be graded as a real answer, and never be
    confused with record_unreachable (the agent DID run)."""

    async def test_empty_handed_timeout_forfeits_not_voids_not_graded(
        self, session_maker
    ) -> None:
        """MUTATION: put TIMEOUT_PARTIAL_MARKER in the persisted content instead
        of the draft buffer. silent_sides then reads non-empty content, the
        side is NOT silent, and the marker string reaches the judge as a real
        answer to grade — the winner assertion and the 'never graded'
        assertion below both go red.
        """
        battle_id, contender_a, contender_b = await _running_battle(
            session_maker, agentic_side_b=True, provider_b="openrouter"
        )

        async def fake_run_agentic_answer(request, on_step):
            # Soft-deadline cutoff with NOTHING drafted -- the empty-handed case.
            await on_step(AgentStep(2, "", True, timed_out=True))
            return None

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.run_agentic_answer",
                side_effect=fake_run_agentic_answer,
            ):
                # The drive itself completing its write is not under test here
                # (that is the internal "did the write reach the DB" signal) --
                # what matters is what got PERSISTED and how settlement reads
                # it, asserted below.
                await runner.drive_contender_submission(
                    await runner.repo.get(battle_id), Side.B, "k", "http://unused"
                )
            await session.commit()

        async with session_maker() as session:
            repo = BattleRepository(session)
            subs = await repo.list_submissions(battle_id)
        final_b = next(s for s in subs if str(s["side"]) == Side.B.value and s["is_final"])
        assert not (final_b["content"] or "").strip(), "timeout content is the draft, empty here"
        assert final_b["error"] == TIMEOUT_PARTIAL_MARKER
        assert not final_b["error"].startswith("provider unreachable"), (
            "a timeout is NOT the same outcome as record_unreachable"
        )

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            silent = await runner.silent_sides(battle_id)
        assert Side.B in silent, "empty content must be read as silent -> forfeit path"

    async def test_marker_string_never_reaches_the_judge_payload(
        self, session_maker
    ) -> None:
        """The exact regression from review: TIMEOUT_PARTIAL_MARKER must never
        appear as gradeable text in what the judge panel is sent.

        MUTATION: revert AgentStep's timeout content to TIMEOUT_PARTIAL_MARKER
        (as it was before the fix). This test's final assertion goes red.
        """
        battle_id, contender_a, contender_b = await _running_battle(
            session_maker, agentic_side_b=True, provider_b="openrouter"
        )

        async def fake_run_agentic_answer(request, on_step):
            await on_step(AgentStep(2, "", True, timed_out=True))
            return None

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.run_agentic_answer",
                side_effect=fake_run_agentic_answer,
            ):
                await runner.drive_contender_submission(
                    await runner.repo.get(battle_id), Side.B, "k", "http://unused"
                )
            await session.commit()

        # Side A silent too (no answer path run) -> both-silent no-contest,
        # settle_silent_forfeit runs and the panel is never called at all --
        # which already proves the marker cannot reach it. Assert directly on
        # what WOULD be sent, for a caller that only fields one silent side:
        # judge_view_by_side must never carry the marker string for any side.
        async with session_maker() as session:
            repo = BattleRepository(session)
            subs = await repo.list_submissions(battle_id)
            final_by_side = {
                str(s["side"]): s["content"] for s in subs if s["is_final"]
            }
            runner = BattleRunner(session, gate=None)
            battle = await repo.get(battle_id)
            view, _max_chars = await runner._judge_view_by_side(battle, subs, final_by_side)
        assert TIMEOUT_PARTIAL_MARKER not in (view.get(Side.B.value) or "")


class TestAgenticContenderUsesItsOwnCredentials:
    """A contender's provider decides what an unresolved id means: an
    OpenRouter model falls back to the caller's own creds (OpenRouter IS the
    default route); a known EXTRA_PROVIDERS model with no key never starts
    the sandbox at all — same distinction _answer_with_model makes on the
    direct-HTTP path, and for the same production incident (see
    _resolve_answer_credentials)."""

    async def test_an_openrouter_contender_falls_back_to_the_callers_creds(
        self, session_maker
    ) -> None:
        """'openrouter' is not an EXTRA_PROVIDERS member, so resolve_provider()
        returning None here is the LEGITIMATE case the fallback exists for.

        MUTATION: drop the fallback_base_url/fallback_api_key wiring in
        _drive_agentic_submission. The captured request's provider_base_url
        stays '' and this test's assertion on fallback_base_url goes red
        (asserting equality with the OpenRouter fallback passed to the drive).
        """
        battle_id, _a, _b = await _running_battle(
            session_maker, agentic_side_b=True, provider_b="openrouter"
        )
        captured = {}

        async def fake_run_agentic_answer(request, on_step):
            captured["request"] = request
            await on_step(AgentStep(2, "an answer", True))
            return "an answer"

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with (
                patch(
                    "app.services.battle_runner.run_agentic_answer",
                    side_effect=fake_run_agentic_answer,
                ),
                patch(
                    "app.services.openrouter_service.OpenRouterService.resolve_provider",
                    return_value=None,
                ),
            ):
                await runner.drive_contender_submission(
                    await runner.repo.get(battle_id),
                    Side.B,
                    "fallback-key",
                    "https://openrouter.ai/api/v1",
                )
            await session.commit()

        req = captured["request"]
        assert req.provider_base_url == ""
        assert req.fallback_base_url == "https://openrouter.ai/api/v1"
        assert req.fallback_api_key == "fallback-key"

    async def test_an_unconfigured_extra_provider_contender_never_starts(
        self, session_maker
    ) -> None:
        """Production: 13 of 31 battles in 8h voided through this exact path —
        zai/glm-4.5-flash (no key on prod), execution_mode='agent'. Starting
        the sandbox with the caller's (judge) credentials sent it to the
        wrong provider entirely, misdiagnosed as an outage.

        MUTATION: drop the _is_extra_provider_unconfigured short-circuit in
        _drive_agentic_submission. run_agentic_answer then gets called (this
        test's mock would be invoked) and this assertion goes red.
        """
        battle_id, _a, side_b = await _running_battle(
            session_maker, agentic_side_b=True, provider_b="zai"
        )

        async def fake_run_agentic_answer(request, on_step):
            raise AssertionError("the sandbox must never start for an unconfigured provider")

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with (
                patch(
                    "app.services.battle_runner.run_agentic_answer",
                    side_effect=fake_run_agentic_answer,
                ),
                patch(
                    "app.services.openrouter_service.OpenRouterService.resolve_provider",
                    return_value=None,
                ),
            ):
                accepted = await runner.drive_contender_submission(
                    await runner.repo.get(battle_id), Side.B, "fallback-key", "http://judge"
                )
            await session.commit()

        assert accepted is False
        async with session_maker() as session:
            subs = await BattleRepository(session).list_submissions(battle_id)
        empty_b = [s for s in subs if str(s["side"]) == Side.B.value and s["is_final"]]
        assert empty_b, "side b recorded a final (empty) submission"
        assert "no credentials configured" in empty_b[0]["error"]

    async def test_a_keyless_but_resolvable_extra_provider_contender_starts(
        self, session_maker
    ) -> None:
        """llm7 is EXTRA_PROVIDERS + key_optional: a REAL member with NO key
        that still resolves (openrouter_service.py:240,292) and must be
        treated exactly like the OpenRouter case, not the unconfigured one.

        resolve_provider is UNPATCHED here on purpose: llm7 resolves for
        real, no key needed — the exact case _is_extra_provider_unconfigured
        must get right by checking resolution, not membership.

        MUTATION: revert _is_extra_provider_unconfigured to bare membership
        (`_provider_prefix(model_id) in EXTRA_PROVIDERS`, no resolution
        check). The sandbox never starts, `captured` stays empty, and the
        KeyError below goes red.
        """
        battle_id, _a, _b = await _running_battle(
            session_maker, agentic_side_b=True, provider_b="llm7"
        )
        captured = {}

        async def fake_run_agentic_answer(request, on_step):
            captured["request"] = request
            await on_step(AgentStep(2, "an answer", True))
            return "an answer"

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.run_agentic_answer",
                side_effect=fake_run_agentic_answer,
            ):
                accepted = await runner.drive_contender_submission(
                    await runner.repo.get(battle_id), Side.B, "fallback-key", "http://judge"
                )
            await session.commit()

        assert accepted is True, "a keyless-but-resolvable provider must start its sandbox"
        req = captured["request"]
        assert req.provider_base_url, "llm7 resolves a real base_url"
        assert req.provider_api_key == "", "llm7 is legitimately keyless"

    async def test_wire_model_keeps_its_provider_prefix(self, session_maker) -> None:
        """agent-runner passes a provider-prefixed id through untouched, but
        silently rewrites a BARE one to the head of its own fallback chain — a
        dead nemotron slug that answers 401. Stripping the prefix here (as the
        model path correctly does) voided every agentic side in production while
        the log blamed the provider.

        MUTATION: wrap the wire_model argument in wire_model_name() again — the
        prefix disappears and this goes red.

        provider_b='openrouter': not an EXTRA_PROVIDERS member, so it must
        reach run_agentic_answer via the legitimate fallback — an unrelated
        concern to this test's own assertion (see
        TestAgenticContenderUsesItsOwnCredentials for the
        unconfigured-extra-provider short-circuit itself).
        """
        battle_id, _a, _b = await _running_battle(
            session_maker, agentic_side_b=True, provider_b="openrouter"
        )
        captured = {}

        async def fake_run_agentic_answer(request, on_step):
            captured["request"] = request
            await on_step(AgentStep(2, "an answer", True))
            return "an answer"

        async with session_maker() as session:
            runner = BattleRunner(session, gate=None)
            with patch(
                "app.services.battle_runner.run_agentic_answer",
                side_effect=fake_run_agentic_answer,
            ):
                await runner.drive_contender_submission(
                    await runner.repo.get(battle_id), Side.B, "k", "https://example.test/v1"
                )
            await session.commit()

        assert "/" in captured["request"].wire_model, (
            f"wire_model must stay provider-prefixed, got {captured['request'].wire_model!r}"
        )
