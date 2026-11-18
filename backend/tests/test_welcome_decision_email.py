"""One plain email when a welcome application is decided, on real PostgreSQL."""

import json
from io import StringIO
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_reward_decisions as cli
from app.core.config import Settings
from app.services import welcome_reward_decision_service as module
from app.services.email_service import EmailService
from app.services.welcome_progress import HOLD_CODES

ledger = _ledger
review = _review

pytestmark = pytest.mark.integration

OWNER_EMAIL = "synthetic@example.com"
FORBIDDEN = [*HOLD_CODES.values(), "hold", "limit", "reserve", "review", "abuse", "check"]
COUNT = "SELECT count(*) FROM reward_eligibility_decisions"


@pytest.fixture
def mailer(monkeypatch):
    fake = AsyncMock()
    fake.send_welcome_decision_email.return_value = True
    monkeypatch.setattr(module, "get_email_service", lambda: fake)
    return fake.send_welcome_decision_email


async def decide(maker, intent, outcome="allow"):
    return await cli.execute(intent.model_copy(update={"outcome": outcome}), True, maker)


async def test_approval_sends_once_after_commit(review, mailer):
    ledger, intent = review
    seen = []

    async def committed(*args):
        async with ledger.maker.begin() as db:
            seen.append(await db.scalar(text(COUNT)))
        return True

    mailer.side_effect = committed
    assert (await decide(ledger.maker, intent))["verdict"] == "recorded"
    mailer.assert_awaited_once_with(OWNER_EMAIL, True)
    assert seen == [1]


async def test_rejection_sends_not_approved(review, mailer):
    ledger, intent = review
    assert (await decide(ledger.maker, intent, "deny"))["verdict"] == "recorded"
    mailer.assert_awaited_once_with(OWNER_EMAIL, False)


async def test_hold_is_not_announced(review, mailer):
    ledger, intent = review
    assert (await decide(ledger.maker, intent, "hold"))["verdict"] == "recorded"
    mailer.assert_not_awaited()


async def test_replay_and_same_outcome_again_do_not_resend(review, mailer):
    ledger, intent = review
    await decide(ledger.maker, intent)
    assert (await decide(ledger.maker, intent))["verdict"] == "replayed"
    again = intent.model_copy(update={"evidence_ref": "synthetic-review-2", "expected_revision": 1})
    assert (await cli.execute(again, True, ledger.maker))["verdict"] == "recorded"
    mailer.assert_awaited_once()


async def test_dry_run_sends_nothing(review, mailer):
    ledger, intent = review
    await cli.execute(intent, False, ledger.maker)
    mailer.assert_not_awaited()


@pytest.mark.parametrize("verified", [False, True])
async def test_only_verified_address_is_mailed(review, mailer, verified):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text(
                "UPDATE users SET is_verified=:v, email_proof_at=NULL, email_proof_email=NULL,"
                " email_proof_source=NULL WHERE id=:o"
            ),
            {"v": verified, "o": ledger.owner},
        )
    bare = intent.model_copy(update={"email_proof_at": None})
    assert (await decide(ledger.maker, bare, "deny"))["verdict"] == "recorded"
    assert mailer.await_count == int(verified)


@pytest.mark.parametrize("failure", ["raises", "returns_false"])
async def test_provider_failure_keeps_decision_and_logs_without_address(review, mailer, failure):
    ledger, intent = review
    if failure == "raises":
        mailer.side_effect = httpx.ConnectError("boom " + OWNER_EMAIL)
    else:
        mailer.return_value = False
    messages = []
    with patch.object(module.logger, "warning", side_effect=lambda *a, **k: messages.append(a)):
        out = await decide(ledger.maker, intent)
    assert out["verdict"] == "recorded"
    async with ledger.maker.begin() as db:
        assert await db.scalar(text(COUNT)) == 1
    assert messages and OWNER_EMAIL not in json.dumps(messages, default=str)


async def test_automatic_run_sends_once_per_approval(review, mailer):
    ledger, _ = review
    settings = Settings.model_construct(
        welcome_auto_min_account_age_days=7, welcome_auto_daily_limit=5
    )
    await cli.run_auto_decide(StringIO(), True, ledger.maker, settings)
    sent = mailer.await_count
    await cli.run_auto_decide(StringIO(), True, ledger.maker, settings)
    assert mailer.await_count == sent >= 1


async def test_clean_approval_sends_once(review, mailer):
    ledger, _ = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet='W' WHERE id=:o"), {"o": ledger.owner}
        )
    code = await cli.run_approve_clean(StringIO(), True, None, ledger.maker)
    first = mailer.await_count
    await cli.run_approve_clean(StringIO(), True, None, ledger.maker)
    assert (code, mailer.await_count) == (0, first)


@pytest.mark.parametrize("approved", [True, False])
async def test_email_text_has_profile_link_and_no_internal_words(approved):
    mail = EmailService.__new__(EmailService)
    mail.settings = Settings.model_construct(frontend_url="https://site.example")
    mail.send_email = AsyncMock(return_value=True)
    await mail.send_welcome_decision_email(OWNER_EMAIL, approved)
    to, subject, html = mail.send_email.await_args.args
    assert to == OWNER_EMAIL and 'href="https://site.example/profile"' in html
    assert ("not approved" in subject) != approved
    assert not [word for word in FORBIDDEN if word in (subject + html).lower()]
