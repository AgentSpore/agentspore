"""Claiming a taken Solana payout address by signing a challenge with its wallet."""

import asyncio
import base64
from types import SimpleNamespace

import pytest
import test_agent_registration_limits
import test_marketplace_tasks
import test_welcome_rewards
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from solders.keypair import Keypair
from sqlalchemy import text

from app.api import deps
from app.api.v1.ownership import router
from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import create_access_token
from app.repositories.payout_repo import PayoutRepository

ledger = test_welcome_rewards.ledger
marketplace_pg = test_marketplace_tasks.marketplace_pg
a3_redis = test_agent_registration_limits.a3_redis
a3_redis_container = test_agent_registration_limits.a3_redis_container

pytestmark = pytest.mark.integration

BASE = "/api/v1/users/solana-wallet"
WRONG_SIGNATURE = "Signature does not match this wallet."
EXPIRED = "Confirmation expired. Request a new one."


def _headers(user_id):
    return {"Authorization": "Bearer " + create_access_token(str(user_id))}


@pytest.fixture
async def api(ledger, a3_redis, monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    monkeypatch.setattr(deps, "async_session_maker", ledger.maker)

    async def database():
        async with ledger.maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_redis] = lambda: a3_redis
    async with ledger.maker.begin() as db:
        await db.execute(
            text("CREATE UNIQUE INDEX users_solana_wallet_key ON users(solana_wallet)")
        )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client,
            ledger=ledger,
            redis=a3_redis,
            owner=_headers(ledger.owner),
            squatter=_headers(ledger.other),
        )
    app.dependency_overrides.clear()


def _sign(key: Keypair, message: str) -> str:
    return base64.b64encode(bytes(key.sign_message(message.encode()))).decode()


async def _wallet_of(c, user_id):
    async with c.ledger.maker.begin() as db:
        return await db.scalar(text("SELECT solana_wallet FROM users WHERE id=:u"), {"u": user_id})


async def _set_wallet(c, user_id, wallet):
    async with c.ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:u"), {"w": wallet, "u": user_id}
        )


async def _challenge(c, headers, wallet):
    reply = await c.client.post(
        BASE + "/challenge", json={"solana_wallet": wallet}, headers=headers
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


async def _claim(c, headers, wallet, signature):
    return await c.client.post(
        BASE + "/claim", json={"solana_wallet": wallet, "signature": signature}, headers=headers
    )


async def _challenge_keys(c, wallet):
    return [k async for k in c.redis.scan_iter("*") if wallet in k]


async def test_owner_takes_back_squatted_address(api):
    key = Keypair()
    wallet = str(key.pubkey())
    await _set_wallet(api, api.ledger.other, wallet)
    taken = await api.client.patch(BASE, json={"solana_wallet": wallet}, headers=api.owner)
    assert taken.status_code == 409

    challenge = await _challenge(api, api.owner, wallet)
    assert wallet in challenge["message"] and str(api.ledger.owner) in challenge["message"]
    assert "example.com" not in challenge["message"]
    reply = await _claim(api, api.owner, wallet, _sign(key, challenge["message"]))

    assert reply.status_code == 200
    assert reply.json() == {"solana_wallet": wallet, "status": "connected"}
    assert await _wallet_of(api, api.ledger.owner) == wallet
    assert await _wallet_of(api, api.ledger.other) is None


async def test_free_address_is_simply_bound(api):
    key = Keypair()
    wallet = str(key.pubkey())
    challenge = await _challenge(api, api.owner, wallet)
    reply = await _claim(api, api.owner, wallet, _sign(key, challenge["message"]))
    assert reply.status_code == 200
    assert await _wallet_of(api, api.ledger.owner) == wallet


async def test_signature_from_another_key_moves_nothing(api):
    wallet = str(Keypair().pubkey())
    await _set_wallet(api, api.ledger.other, wallet)
    challenge = await _challenge(api, api.owner, wallet)
    reply = await _claim(api, api.owner, wallet, _sign(Keypair(), challenge["message"]))
    assert reply.status_code == 400 and reply.json()["detail"] == WRONG_SIGNATURE
    assert await _wallet_of(api, api.ledger.other) == wallet
    assert await _wallet_of(api, api.ledger.owner) is None


async def test_challenge_is_single_use(api):
    key = Keypair()
    wallet = str(key.pubkey())
    challenge = await _challenge(api, api.owner, wallet)
    signature = _sign(key, challenge["message"])
    assert (await _claim(api, api.owner, wallet, signature)).status_code == 200
    await _set_wallet(api, api.ledger.owner, None)
    again = await _claim(api, api.owner, wallet, signature)
    assert again.status_code == 400 and again.json()["detail"] == EXPIRED
    assert await _wallet_of(api, api.ledger.owner) is None


async def test_expired_or_missing_challenge_is_rejected(api):
    key = Keypair()
    wallet = str(key.pubkey())
    challenge = await _challenge(api, api.owner, wallet)
    (redis_key,) = await _challenge_keys(api, wallet)
    assert 0 < await api.redis.ttl(redis_key) <= 600
    await api.redis.delete(redis_key)
    reply = await _claim(api, api.owner, wallet, _sign(key, challenge["message"]))
    assert reply.status_code == 400 and reply.json()["detail"] == EXPIRED


async def test_challenge_cannot_be_redeemed_for_another_wallet(api):
    key, other_key = Keypair(), Keypair()
    other_wallet = str(other_key.pubkey())
    challenge = await _challenge(api, api.owner, str(key.pubkey()))
    reply = await _claim(api, api.owner, other_wallet, _sign(other_key, challenge["message"]))
    assert reply.status_code == 400 and reply.json()["detail"] == EXPIRED
    assert await _wallet_of(api, api.ledger.owner) is None


async def test_challenge_is_bound_to_the_requesting_account(api):
    key = Keypair()
    wallet = str(key.pubkey())
    challenge = await _challenge(api, api.owner, wallet)
    reply = await _claim(api, api.squatter, wallet, _sign(key, challenge["message"]))
    assert reply.status_code == 400 and reply.json()["detail"] == EXPIRED


@pytest.mark.parametrize("signature", ["!!not-base64!!", base64.b64encode(b"short").decode(), ""])
async def test_malformed_signature_is_422_and_keeps_challenge(api, signature):
    wallet = str(Keypair().pubkey())
    await _challenge(api, api.owner, wallet)
    assert (await _claim(api, api.owner, wallet, signature)).status_code == 422
    assert len(await _challenge_keys(api, wallet)) == 1


async def test_invalid_address_is_422_not_409(api):
    reply = await api.client.patch(
        BASE, json={"solana_wallet": "not-an-address"}, headers=api.owner
    )
    assert reply.status_code == 422
    reply = await api.client.post(
        BASE + "/challenge", json={"solana_wallet": "0OIl"}, headers=api.owner
    )
    assert reply.status_code == 422


async def test_challenge_requires_auth(api):
    wallet = str(Keypair().pubkey())
    reply = await api.client.post(BASE + "/challenge", json={"solana_wallet": wallet})
    assert reply.status_code in (401, 403)


async def test_challenge_issuance_is_rate_limited(api):
    wallet = str(Keypair().pubkey())
    for _ in range(get_settings().solana_wallet_challenge_rate_limit):
        await _challenge(api, api.owner, wallet)
    body = {"solana_wallet": wallet}
    assert (
        await api.client.post(BASE + "/challenge", json=body, headers=api.owner)
    ).status_code == 429
    assert (
        await api.client.post(BASE + "/challenge", json=body, headers=api.squatter)
    ).status_code == 200


async def test_concurrent_claims_leave_exactly_one_holder(api):
    key = Keypair()
    wallet = str(key.pubkey())
    signed = []
    for headers in (api.owner, api.squatter):
        challenge = await _challenge(api, headers, wallet)
        signed.append((headers, _sign(key, challenge["message"])))
    replies = await asyncio.gather(*(_claim(api, h, wallet, s) for h, s in signed))
    assert [r.status_code for r in replies] == [200, 200]
    holders = [await _wallet_of(api, u) for u in (api.ledger.owner, api.ledger.other)]
    assert sorted(h is None for h in holders) == [False, True]
    assert wallet in holders


async def test_patch_racing_a_claim_gets_409_not_500(api):
    wallet = str(Keypair().pubkey())
    lock = text("SELECT pg_advisory_xact_lock(hashtextextended(:w, 0))")
    async with api.ledger.maker.begin() as db:
        await db.execute(lock, {"w": wallet})
        patch = asyncio.create_task(
            api.client.patch(BASE, json={"solana_wallet": wallet}, headers=api.squatter)
        )
        await asyncio.wait({patch}, timeout=0.5)
        assert not patch.done()
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:u"),
            {"w": wallet, "u": api.ledger.owner},
        )
    reply = await patch
    assert reply.status_code == 409
    assert await _wallet_of(api, api.ledger.owner) == wallet
    assert await _wallet_of(api, api.ledger.other) is None


async def test_unique_violation_on_patch_is_409(api, monkeypatch):
    wallet = str(Keypair().pubkey())
    await _set_wallet(api, api.ledger.other, wallet)

    async def blind(self, solana_wallet, user_id):
        return False

    monkeypatch.setattr(PayoutRepository, "check_solana_wallet_uniqueness", blind)
    reply = await api.client.patch(BASE, json={"solana_wallet": wallet}, headers=api.owner)
    assert reply.status_code == 409


@pytest.mark.parametrize("path", ["/challenge", "/claim"])
async def test_base58_of_wrong_length_is_422(api, path):
    body = {"solana_wallet": "z" * 44}
    if path == "/claim":
        body["signature"] = base64.b64encode(bytes(64)).decode()
    reply = await api.client.post(BASE + path, json=body, headers=api.owner)
    assert reply.status_code == 422
    assert await api.redis.keys("solana_claim:*") == []


async def test_challenge_names_the_site_and_issue_time(api):
    message = (await _challenge(api, api.owner, str(Keypair().pubkey())))["message"]
    assert f"URI: {get_settings().frontend_url}" in message
    assert "Issued At: " in message


async def test_rate_counter_always_has_an_expiry(api):
    await _challenge(api, api.owner, str(Keypair().pubkey()))
    (key,) = await api.redis.keys("solana_claim_rate:*")
    assert 0 < await api.redis.ttl(key) <= 3600
