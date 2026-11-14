"""Welcome card progress: steps, user-facing holds, payout state and the next action."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_welcome_application_api as base
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from sqlalchemy import text

from app.models.user import User
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
from app.services.welcome_progress import hold_reasons

ledger = base.ledger
marketplace_pg = base.marketplace_pg
a3_redis = base.a3_redis
a3_redis_container = base.a3_redis_container
application_api = base.application_api

pytestmark = pytest.mark.integration

ROUTE = base.ROUTE
WALLET = str(Keypair().pubkey())
OTHER_WALLET = str(Keypair().pubkey())
STEP_IDS = ["email", "wallet", "application", "review", "payout"]
MIGRATIONS = [
    "V111__payout_delivery.sql",
    "R__payout_delivery_identity.sql",
    "V112__payout_delivery_claims.sql",
    "R__payout_delivery_claim_identity.sql",
    "V113__payout_delivery_attempts.sql",
    "R__payout_delivery_attempt_identity.sql",
    "V117__payout_delivery_confirmations.sql",
    "R__payout_delivery_confirmation_identity.sql",
]


@pytest.fixture
async def card(application_api):
    c = application_api
    async with c.ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
        await db.execute(text("UPDATE users SET created_at='2021-01-01'"))
    return c


async def sql(c, statement, **params):
    async with c.ledger.maker.begin() as db:
        return await db.execute(text(statement), params)


async def view(c):
    reply = await c.client.get(ROUTE, headers=c.headers)
    assert reply.status_code == 200
    return reply.json()


def done(body):
    return {step["id"] for step in body["steps"] if step["done"]}


async def apply(c):
    assert (await c.client.post(ROUTE + "/application", headers=c.headers)).status_code == 200


async def confirm_email(c):
    await sql(
        c,
        "UPDATE users SET email_proof_email=email, email_proof_at=clock_timestamp(), "
        "email_proof_source='github' WHERE id=:o",
        o=c.ledger.owner,
    )


async def bind_wallet(c, wallet=WALLET):
    await sql(c, "UPDATE users SET solana_wallet=:w WHERE id=:o", w=wallet, o=c.ledger.owner)


async def test_fresh_user_needs_email_then_wallet_in_order(card):
    await apply(card)
    body = await view(card)
    assert [s["id"] for s in body["steps"]] == STEP_IDS
    assert done(body) == {"application"}
    assert body["hold_reasons"] == ["email_not_confirmed", "wallet_missing"]
    assert body["payout"] is None and body["wait_until"] is None
    assert body["next_step"] == "confirm_email"
    assert [(a["id"], a["method"], a["path"]) for a in body["actions"]] == [
        ("email_proof_request", "POST", "/api/v1/auth/email-proof/request"),
        ("email_proof_confirm", "POST", "/api/v1/auth/email-proof/confirm"),
    ]
    await confirm_email(card)
    body = await view(card)
    assert done(body) == {"email", "application"}
    assert body["hold_reasons"] == ["wallet_missing"] and body["next_step"] == "bind_wallet"
    assert [(a["method"], a["path"]) for a in body["actions"]] == [
        ("PATCH", "/api/v1/users/solana-wallet")
    ]


async def test_complete_profile_waits_for_review_without_holds(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    body = await view(card)
    assert done(body) == {"email", "wallet", "application"}
    assert body["hold_reasons"] == [] and body["next_step"] == "wait_review"
    assert body["actions"] == []


async def test_unapplied_eligible_user_is_pointed_to_apply(card):
    body = await view(card)
    assert body["reason"] == "not_applied" and body["next_step"] == "confirm_email"
    await confirm_email(card)
    await bind_wallet(card)
    body = await view(card)
    assert body["next_step"] == "apply" and done(body) == {"email", "wallet"}
    assert [(a["method"], a["path"]) for a in body["actions"]] == [("POST", ROUTE + "/application")]


async def test_disposable_email_and_young_github_account_are_reported(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    await sql(
        card, "UPDATE users SET email='someone@mailinator.com' WHERE id=:o", o=card.ledger.owner
    )
    await confirm_email(card)
    body = await view(card)
    assert body["hold_reasons"] == ["disposable_email"]
    assert body["next_step"] == "change_email"
    assert "email" not in done(body)
    assert [(a["method"], a["path"]) for a in body["actions"]] == [
        ("POST", "/api/v1/users/me/email/change"),
        ("POST", "/api/v1/users/me/email/change/confirm"),
    ]
    await sql(
        card,
        "UPDATE users SET email='synthetic@example.com', "
        "oauth_provider='github', oauth_id='1001', "
        "oauth_account_created_at=clock_timestamp() - interval '2 days' WHERE id=:o",
        o=card.ledger.owner,
    )
    await confirm_email(card)
    body = await view(card)
    assert body["hold_reasons"] == ["github_too_new"] and body["next_step"] == "wait_review"
    assert body["wait_until"] is not None


async def test_unknown_github_account_age_is_a_hold_with_a_relink_action(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    await sql(
        card,
        "UPDATE users SET oauth_provider='github', oauth_id='1001', "
        "oauth_account_created_at=NULL WHERE id=:o",
        o=card.ledger.owner,
    )
    body = await view(card)
    assert body["hold_reasons"] == ["github_age_unknown"]
    assert body["next_step"] == "relink_github"
    assert [(a["method"], a["path"]) for a in body["actions"]] == [("GET", "/api/v1/oauth/github")]


@pytest.mark.parametrize("outcome", ["allow", "hold"])
async def test_decided_application_waits_for_payout_not_review(card, outcome):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    key = WelcomeBeneficiaryService.email_key("synthetic@example.com")
    await sql(
        card,
        "INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,"
        "policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,expires_at) "
        "SELECT id,owner_user_id,source_event,'welcome-v1',:outcome,:key,'operator','ref',"
        "clock_timestamp()+interval '1 hour' FROM welcome_reward_grants",
        outcome=outcome,
        key=key,
    )
    body = await view(card)
    assert "review" in done(body) or outcome == "hold"
    assert body["next_step"] == ("wait_payout" if outcome == "allow" else "wait_review")


async def test_email_change_is_not_offered_once_the_grant_is_decided(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    await sql(
        card, "UPDATE users SET email='someone@mailinator.com' WHERE id=:o", o=card.ledger.owner
    )
    await confirm_email(card)
    await sql(
        card,
        "INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,"
        "policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,expires_at) "
        "SELECT id,owner_user_id,source_event,'welcome-v1','allow',:key,'operator','ref',"
        "clock_timestamp()+interval '1 hour' FROM welcome_reward_grants",
        key=WelcomeBeneficiaryService.email_key("someone@mailinator.com"),
    )
    body = await view(card)
    assert "disposable_email" in body["hold_reasons"]
    assert body["next_step"] == "wait_payout"
    assert body["actions"] == []


async def test_expired_allow_is_not_a_finished_review(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    key = WelcomeBeneficiaryService.email_key("synthetic@example.com")
    await sql(
        card,
        "INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,"
        "policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,issued_at,expires_at) "
        "SELECT id,owner_user_id,source_event,'welcome-v1','allow',:key,'operator','ref',"
        "clock_timestamp()-interval '3 days',clock_timestamp()-interval '2 days' "
        "FROM welcome_reward_grants",
        key=key,
    )
    body = await view(card)
    assert "review" not in done(body) and body["next_step"] == "wait_review"


async def test_wallet_already_rewarded_is_a_hold_with_a_way_out(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    await sql(
        card,
        "INSERT INTO welcome_reward_grants(program_id,owner_user_id,source_event) "
        "VALUES('welcome',:o,:o)",
        o=card.ledger.other,
    )
    await add_intent(card, card.ledger.other, WALLET)
    body = await view(card)
    assert body["hold_reasons"] == ["wallet_already_used"] and "wallet" not in done(body)
    assert body["next_step"] == "bind_wallet"
    assert WALLET not in str(body)


async def add_intent(c, grant_owner, recipient):
    keys = [str(Pubkey.from_bytes(bytes([n]) * 32)) for n in range(1, 8)]
    await sql(
        c,
        "INSERT INTO payout_delivery_intents(intent_id,producer_namespace,allocation_id,"
        "genesis_hash,mint,source,destination,authority,fee_payer,recipient,program,decimals,"
        "raw_amount) SELECT gen_random_uuid(),'welcome',g.id::text,:k1,:k2,:k3,:k4,:k5,:k5,:r,"
        "'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb',6,'10000000000' "
        "FROM welcome_reward_grants g WHERE g.owner_user_id=:o",
        k1=keys[0],
        k2=keys[1],
        k3=keys[2],
        k4=keys[3],
        k5=keys[4],
        r=recipient,
        o=grant_owner,
    )


async def prepare_attempt(c):
    """Give the intent a prepared claim and a signed attempt with a fixed signature."""
    worker = uuid4()
    async with c.ledger.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO payout_delivery_claims SELECT intent_id,1,'claimed',:w,"
                "clock_timestamp()+interval '5 minutes' FROM payout_delivery_intents"
            ),
            {"w": worker},
        )
        await db.execute(text("UPDATE payout_delivery_claims SET state='prepared'"))
        await db.execute(
            text(
                "INSERT INTO payout_delivery_attempts SELECT intent_id,1,:w,'\\x01'::bytea,"
                "decode(repeat('ab',64),'hex'),:h,'0' FROM payout_delivery_claims"
            ),
            {"w": worker, "h": str(Pubkey.from_bytes(bytes([9]) * 32))},
        )


async def test_payout_states_and_signature(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    await add_intent(card, card.ledger.owner, WALLET)
    body = await view(card)
    assert body["payout"] == {"state": "pending", "signature": None}
    assert body["next_step"] == "wait_payout" and body["hold_reasons"] == []
    assert "payout" not in done(body)
    await prepare_attempt(card)
    sent = (await view(card))["payout"]
    assert sent["state"] == "sent" and sent["signature"]
    await sql(
        card,
        "INSERT INTO payout_delivery_confirmations(intent_id,finalized_slot,verifier_version,"
        "evidence_digest) SELECT intent_id,'1',1,repeat('a',64) FROM payout_delivery_attempts",
    )
    body = await view(card)
    assert body["payout"] == {"state": "confirmed", "signature": sent["signature"]}
    assert "payout" in done(body) and body["next_step"] == "none"


async def test_response_never_carries_keys_or_other_users_data(card):
    await apply(card)
    await confirm_email(card)
    await bind_wallet(card)
    await add_intent(card, card.ledger.owner, WALLET)
    await sql(
        card, "UPDATE users SET solana_wallet=:w WHERE id=:o", w=OTHER_WALLET, o=card.ledger.other
    )
    body = await view(card)
    text_body = str(body)
    assert OTHER_WALLET not in text_body and "other@example.com" not in text_body
    assert WelcomeBeneficiaryService.email_key("synthetic@example.com") not in text_body
    assert set(body) == {
        "state", "reason", "amount_raw", "decimals", "asset", "next_action", "steps",
        "hold_reasons", "payout", "wait_until", "next_step", "actions",
    }  # fmt: skip


def test_a_hold_without_a_user_code_still_blocks(monkeypatch):
    found = SimpleNamespace(holds=("future_hold", "future_hold"))
    monkeypatch.setattr(WelcomeBeneficiaryService, "evaluate", lambda user, now: found)
    user = User(solana_wallet=WALLET)
    assert hold_reasons(user, datetime.now(UTC), False) == ["other_hold"]
