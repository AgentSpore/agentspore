"""Operator review, approve-clean and the pending signal on real PostgreSQL."""

import json
from io import StringIO
from types import SimpleNamespace

import pytest
from solders.keypair import Keypair
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_auto_decide import MANUAL_INSERT
from test_welcome_progress import add_intent
from test_welcome_reward_decisions import OWNER_KEY
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_reward_decisions as cli
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.welcome_reward_service import WelcomeRewardService

ledger = _ledger
review = _review

pytestmark = pytest.mark.integration

WALLET = str(Keypair().pubkey())


@pytest.fixture
async def pending(review):
    """Owner is clean (wallet bound); the other user has no wallet, so it is held."""
    ledger, _ = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:o"), {"w": WALLET, "o": ledger.owner}
        )
    return ledger


async def invoke(runner, maker, *args):
    destination = StringIO()
    code = await runner(destination, *args, session_factory=maker)
    return code, destination.getvalue()


async def count(maker, sql="SELECT count(*) FROM reward_eligibility_decisions"):
    async with maker.begin() as db:
        return await db.scalar(text(sql))


async def test_review_masks_email_and_shows_holds(pending):
    code, out = await invoke(cli.run_review, pending.maker)
    assert code == 0
    assert "synthetic@example.com" not in out and "other@example.com" not in out
    assert "s***@example.com" in out and "o***@example.com" in out
    assert "wallet_missing" in out and out.rstrip().endswith("pending=2 shown=2 clean=1")
    assert WALLET not in out


async def test_dry_run_writes_nothing_and_apply_approves_only_clean_once(pending):
    code, out = await invoke(cli.run_approve_clean, pending.maker, False, None)
    assert code == 0 and json.loads(out) == {
        "verdict": "approved_clean", "approved": 1, "applied": False, "left": 1,
    }  # fmt: skip
    assert await count(pending.maker) == 0
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 1 and await count(pending.maker) == 1
    async with pending.maker.begin() as db:
        owner = await db.scalar(text("SELECT owner_user_id FROM reward_eligibility_decisions"))
    assert owner == pending.owner
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 0 and await count(pending.maker) == 1


async def test_max_limits_approvals(pending):
    async with pending.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:o"),
            {"w": str(Keypair().pubkey()), "o": pending.other},
        )
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, 1)
    assert json.loads(out)["approved"] == 1 and await count(pending.maker) == 1


async def test_duplicate_email_and_held_applications_are_not_approved(pending):
    async with pending.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w, email='Synthetic+x@example.com' WHERE id=:o"),
            {"w": str(Keypair().pubkey()), "o": pending.other},
        )
    code, out = await invoke(cli.run_review, pending.maker)
    assert out.count("yes") >= 2 and out.rstrip().endswith("clean=0")
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 0 and await count(pending.maker) == 0


async def test_pending_signal_has_counters_only(pending):
    code, out = await invoke(cli.run_pending_signal, pending.maker)
    assert code == 0 and json.loads(out) == {"alert": "welcome_pending", "pending": 2, "clean": 1}
    await invoke(cli.run_approve_clean, pending.maker, True, None)
    async with pending.maker.begin() as db:
        await db.execute(
            text("""INSERT INTO reward_eligibility_decisions
                (grant_id,owner_user_id,source_event,policy_version,outcome,beneficiary_key,
                 authority_ref,evidence_ref,expires_at)
                SELECT id,owner_user_id,source_event,'welcome-v1','hold','k','operator','held',
                       clock_timestamp()+interval '1 hour'
                FROM welcome_reward_grants WHERE owner_user_id=:o"""),
            {"o": pending.other},
        )
    code, out = await invoke(cli.run_pending_signal, pending.maker)
    assert code == 0 and out == ""


@pytest.mark.parametrize(
    "arguments",
    [["review", "x"], ["pending-signal", "x"], ["approve-clean", "--max"],
     ["approve-clean", "--max", "0"], ["approve-clean", "--max", "-1"],
     ["approve-clean", "--apply", "--apply"], ["approve-clean", "extra"]],
)  # fmt: skip
def test_bad_arguments_are_rejected_before_any_work(arguments):
    destination = StringIO()
    assert cli.main(arguments, StringIO(), destination) == 5
    assert destination.getvalue() == '{"verdict": "invalid_input"}\n'


async def grant_of(maker, user):
    async with maker.begin() as db:
        return await db.scalar(
            text("SELECT id FROM welcome_reward_grants WHERE owner_user_id=:o"), {"o": user}
        )


async def test_key_allowed_on_another_grant_is_skipped(pending):
    other = await grant_of(pending.maker, pending.other)
    async with pending.maker.begin() as db:
        await db.execute(
            text(MANUAL_INSERT), {"g": other, "o": pending.other, "outcome": "allow",
                                   "key": OWNER_KEY, "ref": "dup"}
        )  # fmt: skip
    code, out = await invoke(cli.run_review, pending.maker)
    assert out.rstrip().endswith("clean=0")
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 0 and await count(pending.maker) == 1


async def test_wallet_with_a_welcome_payout_intent_is_skipped(pending):
    await add_intent(SimpleNamespace(ledger=pending), pending.other, WALLET)
    code, out = await invoke(cli.run_review, pending.maker)
    assert "wallet_already_used" in out
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 0 and await count(pending.maker) == 0


async def test_manual_decision_between_read_and_approve_leaves_the_row(pending):
    grant = await grant_of(pending.maker, pending.owner)
    async with pending.maker.begin() as db:
        await db.execute(
            text(MANUAL_INSERT), {"g": grant, "o": pending.owner, "outcome": "hold",
                                   "key": OWNER_KEY, "ref": "manual"}
        )  # fmt: skip
    async with pending.maker() as db:
        assert await cli.approve_clean_one(db, grant, True) == {"outcome": "left"}
    assert await count(pending.maker) == 1


@pytest.fixture
def min_age(monkeypatch):
    settings = SimpleNamespace(welcome_auto_min_account_age_days=7)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


async def test_approve_clean_enforces_the_configured_account_age(pending, min_age):
    async with pending.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET oauth_provider=NULL, oauth_id=NULL, "
                 "created_at=clock_timestamp() WHERE id=:o"),
            {"o": pending.owner},
        )  # fmt: skip
    code, out = await invoke(cli.run_review, pending.maker)
    assert "account_too_young" in out and out.rstrip().endswith("clean=0")
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 0 and await count(pending.maker) == 0
    async with pending.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET created_at=clock_timestamp() - interval '8 days' WHERE id=:o"),
            {"o": pending.owner},
        )
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 1


async def test_expired_allow_is_listed_and_reapproved_through_the_writer(pending):
    async with pending.maker.begin() as db:
        await db.execute(text("UPDATE reward_programs SET enabled=true"))
        await db.execute(
            text("INSERT INTO reward_funding_entries(allocation_id,program_id,amount_raw,"
                 "evidence_ref,authority_ref) VALUES(gen_random_uuid(),'welcome',"
                 "50000000000,'f','a')")
        )  # fmt: skip
    await invoke(cli.run_approve_clean, pending.maker, True, None)
    async with pending.maker.begin() as db:
        await db.execute(text("ALTER TABLE reward_eligibility_decisions DISABLE TRIGGER USER"))
        await db.execute(
            text("UPDATE reward_eligibility_decisions SET issued_at=issued_at - interval '2 days',"
                 " expires_at=expires_at - interval '2 days'")
        )  # fmt: skip
        await db.execute(text("ALTER TABLE reward_eligibility_decisions ENABLE TRIGGER USER"))
    async with pending.maker.begin() as db:
        stuck = await WelcomeRewardService(WelcomeRewardRepository(db)).advance(pending.owner)
    assert stuck.state == "application"
    code, out = await invoke(cli.run_review, pending.maker)
    assert "expired" in out and out.rstrip().endswith("pending=2 shown=2 clean=1")
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert json.loads(out)["approved"] == 1 and await count(pending.maker) == 2
    async with pending.maker.begin() as db:
        linked = await WelcomeRewardService(WelcomeRewardRepository(db)).advance(pending.owner)
    assert linked.state == "frozen"


async def test_error_mid_batch_reports_approvals_so_far(pending, monkeypatch):
    async with pending.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:o"),
            {"w": str(Keypair().pubkey()), "o": pending.other},
        )
    calls = []
    real = cli.approve_clean_one

    async def flaky(db, grant_id, apply):
        calls.append(grant_id)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return await real(db, grant_id, apply)

    monkeypatch.setattr(cli, "approve_clean_one", flaky)
    code, out = await invoke(cli.run_approve_clean, pending.maker, True, None)
    assert code == 8 and json.loads(out) == {
        "verdict": "storage_error",
        "approved": 1,
        "applied": True,
    }


def test_approve_clean_defaults_to_twenty_rows(monkeypatch):
    seen = []

    async def fake(destination, apply, limit):
        seen.append(limit)
        return 0

    monkeypatch.setattr(cli, "run_approve_clean", fake)
    assert cli.main_approve_clean([], StringIO()) == 0
    assert cli.main_approve_clean(["--max", "3"], StringIO()) == 0
    assert seen == [20, 3]
