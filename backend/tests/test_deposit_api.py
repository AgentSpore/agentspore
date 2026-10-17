"""Actual HTTP/service/repository; synthetic identity override and RPC-only mocking."""

import asyncio
import json
from functools import partial
from types import SimpleNamespace
from typing import TypedDict
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from sqlalchemy import text
from test_payout_deposit_db import deposit_db as deposit_db
from test_payout_deposit_db import marketplace_pg as marketplace_pg
from test_payout_deposit_db import unique_deposit_db as unique_deposit_db

from app.api.deps import get_current_user
from app.api.v1.ownership import router
from app.core.database import get_db
from app.services import payout_service

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class DepositRpcControl(TypedDict):
    """Mutable external-RPC fault and synchronization settings for these tests."""

    barrier: asyncio.Barrier | None
    requests: list[dict[str, object]]
    response: Response | None
    failure: httpx.HTTPError | None


@pytest.fixture
def deposit_sender():
    return Keypair()


@pytest.fixture
def deposit_transaction(deposit_sender):
    source, destination = str(Pubkey.new_unique()), str(Pubkey.new_unique())
    wallet = str(deposit_sender.pubkey())
    tx = str(deposit_sender.sign_message(b"Synthetic transaction identifier"))
    balances = [
        {
            "accountIndex": i,
            "mint": payout_service.ASPORE_TOKEN_MINT,
            "owner": owner,
            "uiTokenAmount": {"amount": amount, "decimals": 6, "uiAmountString": ui},
        }
        for i, owner, amount, ui in [
            (1, wallet, "9000000", "9"),
            (2, payout_service.TREASURY_WALLET, "1000000", "1"),
        ]
    ]
    post = json.loads(json.dumps(balances))
    post[0]["uiTokenAmount"].update(amount="6000000", uiAmountString="6")
    post[1]["uiTokenAmount"].update(amount="4000000", uiAmountString="4")
    instruction = {
        "program": "spl-token",
        "programId": TOKEN_PROGRAM,
        "parsed": {
            "type": "transferChecked",
            "info": {
                "source": source,
                "destination": destination,
                "authority": wallet,
                "mint": payout_service.ASPORE_TOKEN_MINT,
                "tokenAmount": {"amount": "3000000", "decimals": 6},
            },
        },
    }
    return {
        "slot": 1,
        "transaction": {
            "signatures": [tx],
            "message": {
                "accountKeys": [
                    {"pubkey": wallet, "signer": True},
                    {"pubkey": source, "signer": False},
                    {"pubkey": destination, "signer": False},
                    {"pubkey": TOKEN_PROGRAM, "signer": False},
                    {"pubkey": payout_service.ASPORE_TOKEN_MINT, "signer": False},
                ],
                "instructions": [instruction],
            },
        },
        "meta": {
            "err": None,
            "preTokenBalances": balances,
            "postTokenBalances": post,
            "innerInstructions": [],
        },
    }


@pytest.fixture
def deposit_claim(deposit_sender, deposit_transaction, deposit_db):
    _, owner, _ = deposit_db

    def sign(user=None, tx=None, signer=None):
        signer = signer or deposit_sender
        user = owner if user is None else user
        tx = tx or deposit_transaction["transaction"]["signatures"][0]
        wallet = str(signer.pubkey())
        message = (
            "AgentSpore ASPORE deposit claim v1\nnetwork:solana:mainnet-beta\n"
            f"user_id:{user}\ntx_signature:{tx}\nsolana_wallet:{wallet}\n"
            f"mint:{payout_service.ASPORE_TOKEN_MINT}\ntreasury:{payout_service.TREASURY_WALLET}"
        )
        return {
            "tx_signature": tx,
            "solana_wallet": wallet,
            "claim_signature": str(signer.sign_message(message.encode("utf-8"))),
        }

    return sign


@pytest.fixture
def signed_deposit(deposit_claim):
    return deposit_claim()


@pytest.fixture
def deposit_quantity(deposit_transaction):
    def set_raw(raw):
        meta = deposit_transaction["meta"]
        meta["preTokenBalances"][0]["uiTokenAmount"]["amount"] = str(raw + 6000000)
        meta["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "6000000"
        meta["postTokenBalances"][1]["uiTokenAmount"]["amount"] = str(raw + 1000000)
        info = deposit_transaction["transaction"]["message"]["instructions"][0]["parsed"]["info"]
        info["tokenAmount"]["amount"] = str(raw)

    return set_raw


@pytest.fixture
async def deposit_api(unique_deposit_db, deposit_transaction, monkeypatch):
    maker, user, _ = unique_deposit_db
    control: DepositRpcControl = {
        "barrier": None,
        "requests": [],
        "response": None,
        "failure": None,
    }

    async def rpc(request):
        failure = control["failure"]
        if failure is not None:
            raise failure
        payload = json.loads(request.content)
        control["requests"].append(payload)
        barrier = control["barrier"]
        if barrier is not None:
            await barrier.wait()
        response = control["response"]
        if response is not None:
            return response
        return Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": deposit_transaction}
        )

    monkeypatch.setattr(httpx, "AsyncClient", partial(AsyncClient, transport=MockTransport(rpc)))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def request_db():
        async with maker.begin() as db:
            yield db

    async def synthetic_user(request: Request):
        return SimpleNamespace(id=UUID(request.headers.get("X-Test-User", str(user))))

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_current_user] = synthetic_user
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client, control, app


async def test_deposit_signature_cannot_be_claimed_by_another_user(
    deposit_api,
    signed_deposit,
    deposit_db,
):
    client, _, _ = deposit_api
    maker, _, other = deposit_db
    response = await client.post(
        "/api/v1/users/me/aspore/deposit", json=signed_deposit, headers={"X-Test-User": str(other)}
    )
    async with maker() as db:
        balance = await db.scalar(text("SELECT sum(aspore_balance) FROM users"))
        count = await db.scalar(text("SELECT count(*) FROM aspore_transactions"))
    assert (response.status_code, balance, count) == (400, 0, 0)


async def test_same_deposit_cannot_credit_twice_concurrently(
    deposit_api,
    signed_deposit,
    deposit_db,
):
    client, control, _ = deposit_api
    maker, _, _ = deposit_db
    control["barrier"] = asyncio.Barrier(2)
    async with asyncio.TaskGroup() as group:
        first = group.create_task(
            client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
        )
        second = group.create_task(
            client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
        )
    async with maker() as db:
        balance = await db.scalar(text("SELECT sum(aspore_balance) FROM users"))
        count = await db.scalar(text("SELECT count(*) FROM aspore_transactions"))
    statuses = sorted([first.result().status_code, second.result().status_code])
    assert (statuses, balance, count) == ([200, 400], 3, 1)


async def test_valid_direct_deposit_and_legacy_duplicate(deposit_api, signed_deposit):
    client, control, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 200
    assert response.json() == {
        "status": "deposited",
        "amount": 3,
        "balance_after": 3,
        "tx_signature": signed_deposit["tx_signature"],
    }
    assert control["requests"][0]["params"][1]["commitment"] == "finalized"
    repeat = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert repeat.status_code == 400
    assert repeat.json() == {"detail": "Transaction already processed"}
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 3}


async def test_unsigned_profile_address_is_not_authorization(deposit_api, signed_deposit):
    client, control, _ = deposit_api
    signed_deposit["claim_signature"] = str(Keypair().sign_message(b"Unrelated claim"))
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert control["requests"] == []
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}


async def test_deposit_authentication_is_required(deposit_api, signed_deposit):
    client, _, app = deposit_api
    app.dependency_overrides.pop(get_current_user)
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code in {401, 403}


@pytest.mark.parametrize(
    "patch",
    [
        {"message": "Client-selected authorization"},
        {"solana_wallet": "invalid"},
        {"claim_signature": "invalid"},
        {"tx_signature": "invalid"},
        {"claim_signature": 42},
        {"solana_wallet": None},
    ],
)
async def test_deposit_request_validation(deposit_api, signed_deposit, patch):
    client, control, _ = deposit_api
    response = await client.post(
        "/api/v1/users/me/aspore/deposit", json={**signed_deposit, **patch}
    )
    assert response.status_code == 422
    assert control["requests"] == []


async def test_other_signed_transaction_cannot_reuse_claim(deposit_api, signed_deposit):
    client, control, _ = deposit_api
    signed_deposit["tx_signature"] = str(Keypair().sign_message(b"Another transaction"))
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert control["requests"] == []


async def test_distinct_users_cannot_claim_same_transfer(
    deposit_api,
    signed_deposit,
    deposit_db,
    deposit_claim,
):
    client, control, _ = deposit_api
    maker, _, other = deposit_db
    control["barrier"] = asyncio.Barrier(2)
    async with asyncio.TaskGroup() as group:
        first = group.create_task(
            client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
        )
        second = group.create_task(
            client.post(
                "/api/v1/users/me/aspore/deposit",
                json=deposit_claim(user=other),
                headers={"X-Test-User": str(other)},
            )
        )
    async with maker() as db:
        balance = await db.scalar(text("SELECT sum(aspore_balance) FROM users"))
        count = await db.scalar(text("SELECT count(*) FROM aspore_transactions"))
    assert (sorted([first.result().status_code, second.result().status_code]), balance, count) == (
        [200, 400],
        3,
        1,
    )


async def test_signed_wallet_cannot_claim_another_source(deposit_api, deposit_claim):
    client, _, _ = deposit_api
    claim = deposit_claim(signer=Keypair())
    response = await client.post("/api/v1/users/me/aspore/deposit", json=claim)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}
