"""Operator review, approve-clean and the pending signal on real PostgreSQL."""

import json
from io import StringIO

import pytest
from solders.keypair import Keypair
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_reward_decisions as cli

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
