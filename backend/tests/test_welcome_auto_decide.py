"""Automatic welcome approvals on real PostgreSQL; limits are arbitrary synthetic values."""

import asyncio
import functools
import json
from datetime import timedelta
from io import StringIO
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import OWNER_KEY
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_reward_decisions as cli
from app.core.config import Settings
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.welcome_reward_service import WelcomeRewardService

ledger = _ledger
review = _review

pytestmark = pytest.mark.integration

AGE_DAYS = 7
DAILY = 5
MANUAL_INSERT = """INSERT INTO reward_eligibility_decisions
    (grant_id,owner_user_id,source_event,policy_version,outcome,beneficiary_key,
     authority_ref,evidence_ref,expires_at)
    VALUES (:g,:o,:o,'welcome-v1',:outcome,:key,'operator',:ref,
            clock_timestamp()+interval '1 hour')"""


async def invoke(maker, apply=True, age=AGE_DAYS, daily=DAILY):
    settings = Settings.model_construct(
        welcome_auto_min_account_age_days=age, welcome_auto_daily_limit=daily
    )
    destination = StringIO()
    code = await cli.run_auto_decide(destination, apply, maker, settings)
    return code, json.loads(destination.getvalue())


async def scalar(maker, sql, **params):
    async with maker.begin() as db:
        return await db.scalar(text(sql), params)


async def decisions(maker):
    return await scalar(maker, "SELECT count(*) FROM reward_eligibility_decisions")


async def test_happy_path_approves_each_once_and_rerun_adds_nothing(review):
    ledger, _ = review
    code, out = await invoke(ledger.maker)
    assert (code, out) == (
        0,
        {"verdict": "auto_decided", "approved": 2, "left": 0, "applied": True},
    )
    automatic = await scalar(
        ledger.maker,
        "SELECT count(*) FROM reward_eligibility_decisions "
        "WHERE authority_ref='auto:v1' AND outcome='allow'",
    )
    assert automatic == 2
    code, out = await invoke(ledger.maker)
    assert (code, out["approved"]) == (0, 0)
    assert await decisions(ledger.maker) == 2


async def test_dry_run_writes_nothing(review):
    ledger, _ = review
    code, out = await invoke(ledger.maker, apply=False)
    assert (code, out["approved"], out["applied"]) == (0, 2, False)
    assert await decisions(ledger.maker) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE users SET email_proof_at=NULL",
        "UPDATE users SET email_proof_email='stale@example.com'",
        "UPDATE users SET oauth_provider='google'",
        "UPDATE users SET oauth_account_created_at=NULL",
        "UPDATE users SET oauth_account_created_at=clock_timestamp() - interval '2 days'",
        "UPDATE users SET email='someone@mailinator.com'",
    ],
)
async def test_failed_rule_leaves_application_untouched(review, mutation):
    ledger, _ = review
    async with ledger.maker.begin() as db:
        await db.execute(text(mutation))
    code, out = await invoke(ledger.maker)
    assert (code, out["approved"], out["left"]) == (0, 0, 2)
    assert await decisions(ledger.maker) == 0


@pytest.fixture
def clock_ahead(monkeypatch):
    """Move the decision clock forward; the registration-time grant itself stays untouched."""
    original = WelcomeRewardRepository.decision_clock
    shift = {"by": timedelta()}

    async def shifted(self):
        return await original(self) + shift["by"]

    monkeypatch.setattr(WelcomeRewardRepository, "decision_clock", shifted)
    return shift


async def test_user_without_github_is_aged_by_platform_account_at_decision_time(
    review, clock_ahead
):
    ledger, _ = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text(
                "UPDATE users SET oauth_provider='google', oauth_account_created_at=NULL, "
                "created_at=(SELECT created_at FROM welcome_reward_grants "
                "WHERE owner_user_id=users.id)"
            )
        )
    clock_ahead["by"] = timedelta(days=AGE_DAYS) - timedelta(hours=1)
    code, out = await invoke(ledger.maker)
    assert (code, out["approved"], out["left"]) == (0, 0, 2)
    clock_ahead["by"] = timedelta(days=AGE_DAYS) + timedelta(hours=1)
    code, out = await invoke(ledger.maker)
    assert (code, out["approved"], out["left"]) == (0, 2, 0)


async def test_account_age_threshold_comes_from_configuration(review):
    ledger, _ = review
    code, out = await invoke(ledger.maker, age=5000)
    assert (out["approved"], out["left"]) == (0, 2)
    assert await decisions(ledger.maker) == 0


async def test_manual_decision_is_never_overridden(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text(MANUAL_INSERT),
            {
                "g": intent.grant_id,
                "o": ledger.owner,
                "outcome": "hold",
                "key": None,
                "ref": "manual-hold",
            },
        )
    code, out = await invoke(ledger.maker)
    assert (out["approved"], out["left"]) == (1, 0)
    outcome = await scalar(
        ledger.maker,
        "SELECT outcome FROM reward_eligibility_decisions WHERE evidence_ref='manual-hold'",
    )
    assert outcome == "hold"
    assert await decisions(ledger.maker) == 2


async def test_beneficiary_already_approved_is_left(review):
    ledger, _ = review
    other_grant = await scalar(
        ledger.maker,
        "SELECT id FROM welcome_reward_grants WHERE owner_user_id=:o",
        o=ledger.other,
    )
    async with ledger.maker.begin() as db:
        await db.execute(
            text(MANUAL_INSERT),
            {
                "g": other_grant,
                "o": ledger.other,
                "outcome": "allow",
                "key": OWNER_KEY,
                "ref": "manual-allow",
            },
        )
    code, out = await invoke(ledger.maker)
    assert (out["approved"], out["left"]) == (0, 1)
    assert await decisions(ledger.maker) == 1


async def test_rolling_cap_counts_existing_automatic_decisions(review):
    ledger, _ = review
    code, out = await invoke(ledger.maker, daily=1)
    assert (out["approved"], out["left"]) == (1, 1)
    code, out = await invoke(ledger.maker, daily=1)
    assert (out["approved"], out["left"]) == (0, 1)
    assert await decisions(ledger.maker) == 1
    async with ledger.maker.begin() as db:
        await db.execute(text("ALTER TABLE reward_eligibility_decisions DISABLE TRIGGER USER"))
        await db.execute(
            text(
                "UPDATE reward_eligibility_decisions SET issued_at=issued_at - interval '2 days',"
                " expires_at=expires_at - interval '2 days'"
            )
        )
        await db.execute(text("ALTER TABLE reward_eligibility_decisions ENABLE TRIGGER USER"))
    code, out = await invoke(ledger.maker, daily=1)
    assert out["approved"] == 1


async def test_concurrent_runs_never_exceed_cap(review):
    ledger, _ = review
    async with asyncio.timeout(60):
        results = await asyncio.gather(*(invoke(ledger.maker, daily=1) for _ in range(4)))
    assert [code for code, _ in results] == [0, 0, 0, 0]
    assert sum(out["approved"] for _, out in results) == 1
    assert await decisions(ledger.maker) == 1


@pytest.mark.parametrize("age,daily", [(0, DAILY), (AGE_DAYS, 0), (0, 0)])
async def test_unset_configuration_fails_closed(review, age, daily):
    ledger, _ = review
    code, out = await invoke(ledger.maker, age=age, daily=daily)
    assert (code, out) == (13, {"verdict": "disabled"})
    assert await decisions(ledger.maker) == 0


@pytest.mark.parametrize("field", ["welcome_auto_min_account_age_days", "welcome_auto_daily_limit"])
def test_negative_limits_are_rejected_by_settings(field):
    with pytest.raises(ValidationError):
        Settings.model_validate({field: -1})


async def test_onboarding_application_is_created_while_a_run_is_in_progress(review, monkeypatch):
    ledger, _ = review
    newcomer = uuid4()
    async with ledger.maker.begin() as db:
        await db.execute(
            text("INSERT INTO users(id,email) VALUES(:id,'newcomer@example.com')"), {"id": newcomer}
        )
        await db.execute(text("UPDATE reward_programs SET enabled=true"))
    original = cli.in_session

    async def interleaved(factory, work):
        result = await original(factory, work)
        if isinstance(work, functools.partial):
            async with ledger.maker.begin() as db:
                await WelcomeRewardService(WelcomeRewardRepository(db)).apply_onboarding(newcomer)
        return result

    monkeypatch.setattr(cli, "in_session", interleaved)
    code, out = await invoke(ledger.maker)
    assert (code, out["approved"]) == (0, 2)
    created = await scalar(
        ledger.maker,
        "SELECT count(*) FROM welcome_reward_grants WHERE owner_user_id=:o",
        o=newcomer,
    )
    assert created == 1


async def test_failure_after_a_committed_step_reports_the_approvals_made(review, monkeypatch):
    ledger, _ = review
    original = cli.in_session
    steps = []

    async def second_step_busy(factory, work):
        if not isinstance(work, functools.partial):
            return await original(factory, work)
        steps.append(work)
        return {"verdict": "busy"} if len(steps) == 2 else await original(factory, work)

    monkeypatch.setattr(cli, "in_session", second_step_busy)
    code, out = await invoke(ledger.maker)
    assert (code, out) == (11, {"verdict": "busy", "approved": 1, "applied": True})
    assert await decisions(ledger.maker) == 1


async def test_one_engine_is_shared_by_all_steps(review, monkeypatch):
    ledger, _ = review
    created = []

    def shared_engine(*args, **kwargs):
        created.append(1)
        return ledger.maker.kw["bind"]

    monkeypatch.setattr(cli, "create_async_engine", shared_engine)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings.model_construct(database_url="x"))
    code, out = await invoke(None)
    assert (code, out["approved"], len(created)) == (0, 2, 1)
