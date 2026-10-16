"""Caller-visible proof validation on the real authenticated deposit route."""

import httpx
import pytest
from base58 import b58encode
from httpx import Response
from sqlalchemy import text
from test_deposit_api import deposit_api as deposit_api
from test_deposit_api import deposit_claim as deposit_claim
from test_deposit_api import deposit_db as deposit_db
from test_deposit_api import deposit_quantity as deposit_quantity
from test_deposit_api import deposit_sender as deposit_sender
from test_deposit_api import deposit_transaction as deposit_transaction
from test_deposit_api import marketplace_pg as marketplace_pg
from test_deposit_api import signed_deposit as signed_deposit
from test_deposit_api import unique_deposit_db as unique_deposit_db


async def test_public_transaction_identifier_requires_ownership_proof(
    deposit_api,
    signed_deposit,
    deposit_db,
):
    client, _, _ = deposit_api
    response = await client.post(
        "/api/v1/users/me/aspore/deposit", json={"tx_signature": signed_deposit["tx_signature"]}
    )
    maker, _, _ = deposit_db
    async with maker() as db:
        balance = await db.scalar(text("SELECT sum(aspore_balance) FROM users"))
        count = await db.scalar(text("SELECT count(*) FROM aspore_transactions"))
    assert (response.status_code, balance, count) == (422, 0, 0)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("transaction", "message", "accountKeys", 0, "signer"), False),
        (("transaction", "message", "accountKeys", 0, "signer"), 1),
        (
            ("transaction", "message", "instructions", 0, "programId"),
            "11111111111111111111111111111111",
        ),
        (("transaction", "message", "instructions", 0, "parsed", "info", "authority"), "other"),
        (("transaction", "message", "instructions", 0, "parsed", "info", "source"), "other"),
        (("transaction", "message", "instructions", 0, "parsed", "info", "mint"), "other"),
        (
            (
                "transaction",
                "message",
                "instructions",
                0,
                "parsed",
                "info",
                "tokenAmount",
                "decimals",
            ),
            7,
        ),
        (("transaction", "signatures", 0), "different"),
        (("meta", "err"), {"InstructionError": [0, "Synthetic"]}),
        (("meta", "innerInstructions"), [{"index": 0, "instructions": [{}]}]),
        (("meta", "preTokenBalances", 0, "owner"), "other"),
        (("meta", "postTokenBalances", 1, "owner"), "other"),
        (("meta", "preTokenBalances", 0, "mint"), "other"),
        (("meta", "postTokenBalances", 1, "accountIndex"), 99),
        (("meta", "postTokenBalances", 0, "uiTokenAmount", "amount"), "5999999"),
        (("meta", "postTokenBalances", 1, "uiTokenAmount", "amount"), "4000001"),
        (("meta", "preTokenBalances", 0, "uiTokenAmount", "decimals"), 6.0),
    ],
)
async def test_rpc_transfer_attribution_rejects_tampering(
    deposit_api,
    signed_deposit,
    deposit_transaction,
    path,
    value,
):
    node = deposit_transaction
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}
    assert (await client.get("/api/v1/users/me/aspore/transactions")).json() == []


@pytest.mark.parametrize("kind", ["missing_err", "batch", "missing_mint", "duplicate_index"])
async def test_ambiguous_rpc_shapes_are_rejected(
    deposit_api,
    signed_deposit,
    deposit_transaction,
    kind,
):
    message = deposit_transaction["transaction"]["message"]
    if kind == "missing_err":
        deposit_transaction["meta"].pop("err")
    elif kind == "batch":
        message["instructions"].append(message["instructions"][0])
    elif kind == "missing_mint":
        message["accountKeys"].pop()
    else:
        records = deposit_transaction["meta"]["preTokenBalances"]
        records.append(records[0])
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore/transactions")).json() == []


@pytest.mark.parametrize(
    "kind", ["timeout", "http", "json", "error", "id", "bool_id", "version", "null"]
)
async def test_rpc_failures_do_not_credit(deposit_api, signed_deposit, deposit_transaction, kind):
    client, control, _ = deposit_api
    envelope = {"jsonrpc": "2.0", "id": 1, "result": deposit_transaction}
    if kind == "timeout":
        control["failure"] = httpx.ReadTimeout("Synthetic RPC timeout")
    elif kind == "http":
        control["response"] = Response(503)
    elif kind == "json":
        control["response"] = Response(200, content=b"not JSON")
    else:
        patches = {
            "error": {"error": {"code": -32000}},
            "id": {"id": 2},
            "bool_id": {"id": True},
            "version": {"jsonrpc": "1.0"},
            "null": {"result": None},
        }
        control["response"] = Response(200, json={**envelope, **patches[kind]})
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert response.json() == {"detail": "Unable to verify deposit transaction"}
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}


@pytest.mark.parametrize("raw", [0, 1, 3000001, 1 << 64, ((1 << 63) - 1) * 1000000])
async def test_non_whole_or_out_of_range_amount_is_rejected(
    deposit_api,
    signed_deposit,
    deposit_quantity,
    raw,
):
    deposit_quantity(raw)
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}


async def test_large_raw_amount_uses_exact_integer_fields(
    deposit_api,
    signed_deposit,
    deposit_quantity,
):
    deposit_quantity(9007199255000000)
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 200
    assert response.json()["amount"] == 9007199255
    assert response.json()["balance_after"] == 9007199255


async def test_existing_balance_cannot_overflow(deposit_api, signed_deposit, deposit_db):
    client, _, _ = deposit_api
    maker, owner, _ = deposit_db
    async with maker.begin() as db:
        await db.execute(
            text("UPDATE users SET aspore_balance = :balance WHERE id = :user"),
            {"balance": 9223372036854775806, "user": owner},
        )
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore")).json() == {
        "aspore_balance": 9223372036854775806,
    }
    assert (await client.get("/api/v1/users/me/aspore/transactions")).json() == []


async def test_ledger_failure_rolls_back_credit(deposit_api, signed_deposit, deposit_db):
    client, _, _ = deposit_api
    maker, _, _ = deposit_db
    async with maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION fail_deposit_ledger() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'Synthetic ledger failure'; END; $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER fail_deposit BEFORE INSERT ON aspore_transactions "
                "FOR EACH ROW EXECUTE FUNCTION fail_deposit_ledger()"
            )
        )
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 500
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}
    assert (await client.get("/api/v1/users/me/aspore/transactions")).json() == []


@pytest.mark.parametrize("checked", [False, True])
async def test_supported_transfer_variants(
    deposit_api, signed_deposit, deposit_transaction, checked
):
    message = deposit_transaction["transaction"]["message"]
    if not checked:
        parsed = message["instructions"][0]["parsed"]
        parsed["type"] = "transfer"
        parsed["info"].pop("mint")
        parsed["info"]["amount"] = parsed["info"].pop("tokenAmount")["amount"]
    else:
        program = "ComputeBudget111111111111111111111111111111"
        message["accountKeys"].append({"pubkey": program, "signer": False})
        message["instructions"].insert(
            0,
            {
                "programId": program,
                "accounts": [],
                "data": b58encode(bytes([2, 64, 13, 3, 0])).decode(),
            },
        )
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 200
    assert response.json()["amount"] == 3
