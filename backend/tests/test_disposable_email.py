"""Rotating disposable providers: MX verdict, review holds, approve-clean and RU emails."""

import json
from io import StringIO
from unittest.mock import AsyncMock

import dns.exception
import pytest
from solders.keypair import Keypair
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_reward_decisions as cli
from app.services import disposable_email
from app.services.email_service import EmailService, email_locale

ledger = _ledger
review = _review

ROTATING = "rotating-inbox.example"


@pytest.fixture
def mx(monkeypatch):
    table = {}

    async def fake(domain):
        found = table.get(domain, [])
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(disposable_email, "resolve_mx", fake)
    return table


async def test_mx_of_disposable_provider_is_disposable(mx):
    mx[ROTATING] = ["mx.mail.tm"]
    assert await disposable_email.mx_hold(ROTATING) == "disposable_email"


async def test_mailinator_mx_is_disposable(mx):
    mx[ROTATING] = ["mail.mailinator.com"]
    assert await disposable_email.mx_hold(ROTATING) == "disposable_email"


async def test_ordinary_mx_is_clean_and_lookalike_suffix_is_not_matched(mx):
    mx["good.example"] = ["aspmx.l.google.com", "notmail.tm.example"]
    assert await disposable_email.mx_hold("good.example") is None


async def test_dns_failure_is_unknown_not_clean(mx):
    mx[ROTATING] = dns.exception.Timeout()
    assert await disposable_email.mx_hold(ROTATING) == "dns_unavailable"


async def test_static_blocklist_needs_no_lookup(mx):
    mx["mailinator.com"] = dns.exception.Timeout()
    assert await disposable_email.mx_hold("mailinator.com") == "disposable_email"


@pytest.fixture
async def pending(review):
    ledger, _ = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:o"),
            {"w": str(Keypair().pubkey()), "o": ledger.owner},
        )
    return ledger


async def run(runner, maker, *args):
    destination = StringIO()
    await runner(destination, *args, session_factory=maker)
    return destination.getvalue()


async def decisions(maker):
    async with maker.begin() as db:
        return await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions"))


async def move_owner(ledger, domain):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET email=:e WHERE id=:o"),
            {"e": f"synthetic@{domain}", "o": ledger.owner},
        )


@pytest.mark.integration
async def test_review_flags_rotating_domain_and_approve_clean_skips_it(pending, mx):
    mx[ROTATING] = ["mx.mail.tm"]
    await move_owner(pending, ROTATING)
    out = await run(cli.run_review, pending.maker)
    assert "disposable_email" in out and out.rstrip().endswith("clean=0")
    result = json.loads(await run(cli.run_approve_clean, pending.maker, True, None))
    assert result["approved"] == 0 and await decisions(pending.maker) == 0


@pytest.mark.integration
async def test_dns_failure_shows_reason_and_is_not_approved(pending, mx):
    mx[ROTATING] = dns.exception.Timeout()
    await move_owner(pending, ROTATING)
    out = await run(cli.run_review, pending.maker)
    assert "dns_unavailable" in out and out.rstrip().endswith("clean=0")
    result = json.loads(await run(cli.run_approve_clean, pending.maker, True, None))
    assert result["approved"] == 0 and await decisions(pending.maker) == 0


@pytest.mark.integration
async def test_ordinary_domain_is_still_approved(pending, mx):
    result = json.loads(await run(cli.run_approve_clean, pending.maker, True, None))
    assert result["approved"] == 1


@pytest.mark.parametrize(
    ("base_url", "accept", "expected"),
    [
        ("https://agentspore.ru", None, "ru"),
        ("https://agentspore.com", "ru-RU,ru;q=0.9,en;q=0.8", "ru"),
        ("https://agentspore.com", "en-US,en;q=0.9", "en"),
        (None, None, "en"),
    ],
)
def test_email_locale(base_url, accept, expected):
    assert email_locale(base_url, accept) == expected


async def sent_message(locale):
    service = EmailService()
    service.send_email = AsyncMock(return_value=True)
    await service.send_verification_email("a@b.example", "tok", None, locale)
    return service.send_email.await_args.args[1:]


async def test_russian_verification_email_is_russian():
    subject, html = await sent_message("ru")
    assert subject == "Подтвердите учётную запись AgentSpore"
    assert "Подтвердить" in html and "Verify" not in html


async def test_english_verification_email_is_unchanged():
    subject, html = await sent_message("en")
    assert subject == "Verify your AgentSpore account" and "Verify Email" in html


@pytest.mark.parametrize("approved", [True, False])
async def test_decision_email_is_bilingual_one_message(approved):
    service = EmailService()
    service.send_email = AsyncMock(return_value=True)
    await service.send_welcome_decision_email("a@b.example", approved)
    _, subject, html = service.send_email.await_args.args
    assert " / " in subject and "\u2014" not in subject + html
    assert html.index("Your welcome reward") < html.index("Ваша заявка")
    assert ("sent to your wallet automatically" in html) == approved
    assert ("Токены поступят на ваш кошелёк автоматически" in html) == approved
