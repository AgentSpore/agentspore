"""Prove attempt reconciliation with synthetic keys, RPC fixtures and real PostgreSQL."""

import asyncio
import base64
import io
import json
import threading
from uuid import UUID

import base58
import httpx
import pytest
import test_payout_attempt_db as attempts
from solders.hash import Hash
from solders.signature import Signature
from solders.transaction import Transaction
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.cli.payout_delivery import run_admission, run_reconcile
from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import ClaimKey

native_case = attempts.native_case
allocation = attempts.allocation
signed_envelope = attempts.signed_envelope
attempt_db = attempts.attempt_db
claims_db = attempts.claims_db
delivery_db = attempts.delivery_db
marketplace_pg = attempts.marketplace_pg
MIGRATIONS = attempts.MIGRATIONS
ENDPOINT = "http://rpc.invalid"


def build_receipt(intent, raw, slot=7):
    """Derive a finalized legacy receipt from the stored synthetic wire."""
    transaction = Transaction.from_bytes(raw)
    message = transaction.message
    keys = [str(key) for key in message.account_keys]
    instruction = message.instructions[0]
    balances = [
        {
            "accountIndex": keys.index(account),
            "mint": intent.mint,
            "programId": intent.program,
            "owner": owner,
            "uiTokenAmount": {"amount": amount, "decimals": intent.decimals},
        }
        for account, owner, amount in (
            (intent.source, intent.authority, "20"),
            (intent.destination, intent.recipient, "0"),
        )
    ]
    post = json.loads(json.dumps(balances))
    for balance in post:
        balance["uiTokenAmount"]["amount"] = "10"
    return {
        "slot": slot,
        "version": "legacy",
        "meta": {
            "err": None,
            "fee": 5,
            "innerInstructions": [],
            "preBalances": [20] * len(keys),
            "postBalances": [15] + [20] * (len(keys) - 1),
            "preTokenBalances": balances,
            "postTokenBalances": post,
        },
        "transaction": {
            "signatures": [str(sig) for sig in transaction.signatures],
            "message": {
                "accountKeys": keys,
                "header": {
                    "numRequiredSignatures": message.header.num_required_signatures,
                    "numReadonlySignedAccounts": message.header.num_readonly_signed_accounts,
                    "numReadonlyUnsignedAccounts": message.header.num_readonly_unsigned_accounts,
                },
                "recentBlockhash": str(message.recent_blockhash),
                "instructions": [
                    {
                        "programIdIndex": instruction.program_id_index,
                        "accounts": list(instruction.accounts),
                        "data": base58.b58encode(instruction.data).decode(),
                    }
                ],
            },
        },
    }


class Rpc:
    """Serve JSON-RPC at the HTTP boundary and record every method asked."""

    def __init__(self, genesis, receipt, gate=None):
        self.genesis, self.receipt, self.gate = genesis, receipt, gate
        self.methods: list[str] = []
        self.started = threading.Event()

    def __call__(self, request):
        body = json.loads(request.content)
        self.methods.append(body["method"])
        self.started.set()
        if self.gate is not None:
            self.gate.wait(10)
        result = self.genesis if body["method"] == "getGenesisHash" else self.receipt
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    @property
    def transport(self):
        return httpx.MockTransport(self)


@pytest.fixture
async def prepared(attempt_db, signed_envelope, native_case):
    """A committed prepared attempt, the confirmation schema and a matching receipt."""
    maker, _, _, key = attempt_db
    async with maker.begin() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        for name in (
            "V117__payout_delivery_confirmations.sql",
            "R__payout_delivery_confirmation_identity.sql",
        ):
            await raw.execute((MIGRATIONS / name).read_text())
    assert (await attempts.invoke_attempt(maker, "prepare-commit", signed_envelope))[0] == 0
    intent, _ = native_case
    wire = base64.b64decode(signed_envelope.signed_transaction_base64)
    return maker, key, intent, wire


async def reconcile(maker, key, rpc, **override):
    payload = {
        "intent_id": key["intent_id"],
        "trusted_genesis_hash": str(Hash.default()),
        "rpc_endpoint": ENDPOINT,
    } | override
    out = io.StringIO()
    code = await run_reconcile(io.StringIO(json.dumps(payload)), out, maker, rpc.transport)
    return code, out.getvalue()


async def rows(maker):
    async with maker() as db:
        return await db.scalar(text("SELECT count(*) FROM payout_delivery_confirmations"))


async def show(maker, key):
    out = io.StringIO()
    request = io.StringIO(json.dumps({"intent_id": key["intent_id"]}))
    code = await run_admission("confirmation-show", request, out, maker)
    return code, json.loads(out.getvalue())


async def test_reconcile_uses_persisted_attempt_only(attempt_db, signed_envelope, prepared):
    maker, key, intent, wire = prepared
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire))
    code, text_out = await reconcile(maker, key, rpc, amount="1")
    assert code == 5 and json.loads(text_out) == {"verdict": "invalid_input"}
    missing = {"intent_id": "00000000-0000-4000-8000-000000000000"}
    code, text_out = await reconcile(maker, missing, rpc)
    assert (code, json.loads(text_out)) == (6, {"verdict": "not_found"})
    assert rpc.methods == []


async def test_reconcile_rejects_wrong_network(prepared):
    maker, key, intent, wire = prepared
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire))
    other = str(Hash.from_bytes(bytes([9]) * 32))
    code, out = await reconcile(maker, key, rpc, trusted_genesis_hash=other)
    assert (code, json.loads(out)) == (3, {"verdict": "unsupported"}) and rpc.methods == []
    foreign = Rpc(other, build_receipt(intent, wire))
    assert (await reconcile(maker, key, foreign))[0] == 3
    assert foreign.methods == ["getGenesisHash"] and await rows(maker) == 0


@pytest.mark.parametrize("mutation", ["blockhash", "signature"])
async def test_reconcile_requires_exact_wire_receipt(prepared, mutation):
    maker, key, intent, wire = prepared
    receipt = build_receipt(intent, wire)
    if mutation == "blockhash":
        receipt["transaction"]["message"]["recentBlockhash"] = str(
            Hash.from_bytes(bytes(32)[:31] + b"\1")
        )
    elif len(receipt["transaction"]["signatures"]) == 1:
        pytest.skip("shared payer has no second signature to alter")
    else:
        receipt["transaction"]["signatures"][1] = str(Signature.default())
    code, _ = await reconcile(maker, key, Rpc(intent.genesis_hash, receipt))
    assert code == 3 and await rows(maker) == 0


def break_amount(receipt):
    receipt["meta"]["postTokenBalances"][1]["uiTokenAmount"]["amount"] = "9"


def break_err(receipt):
    receipt["meta"]["err"] = {"InstructionError": [0, "Custom"]}


def break_slot(receipt):
    receipt["slot"] = "7"


def break_balances(receipt):
    receipt["meta"]["postTokenBalances"].pop()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (break_amount, 3),
        (break_err, 4),
        (break_slot, 2),
        (break_balances, 2),
        (lambda receipt: receipt["meta"].update(fee=6), 3),
        (None, 2),
    ],
)
async def test_reconcile_requires_matching_finalized_evidence(prepared, mutate, code):
    maker, key, intent, wire = prepared
    receipt = build_receipt(intent, wire)
    if mutate is None:
        receipt = None
    else:
        mutate(receipt)
    result, out = await reconcile(maker, key, Rpc(intent.genesis_hash, receipt))
    assert result == code and await rows(maker) == 0
    assert (await show(maker, key))[1] == {"verdict": "not_found"}


async def test_reconcile_concurrent_exact_replay(prepared):
    maker, key, intent, wire = prepared
    receipt = build_receipt(intent, wire)
    results = await asyncio.gather(
        *(reconcile(maker, key, Rpc(intent.genesis_hash, receipt)) for _ in range(2))
    )
    verdicts = {json.loads(out)["verdict"] for _, out in results}
    assert "confirmed" in verdicts and verdicts <= {"confirmed", "replayed", "busy"}
    assert await rows(maker) == 1
    again = await reconcile(maker, key, Rpc(intent.genesis_hash, receipt))
    assert json.loads(again[1])["verdict"] == "replayed" and await rows(maker) == 1


async def test_confirmation_conflict_cannot_overwrite_and_lost_ack_lookup(prepared):
    maker, key, intent, wire = prepared
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire, slot=7))
    code, out = await reconcile(maker, key, rpc)
    first = json.loads(out)
    assert code == 0 and first["verdict"] == "confirmed" and first["finalized_slot"] == "7"
    assert await show(maker, key) == (0, first | {"verdict": "found"})
    other = Rpc(intent.genesis_hash, build_receipt(intent, wire, slot=8))
    code, out = await reconcile(maker, key, other)
    assert code == 7 and json.loads(out)["verdict"] == "conflict"
    assert (await show(maker, key))[1]["finalized_slot"] == "7"
    for lookup in (None, "failed"):
        receipt = None if lookup is None else build_receipt(intent, wire)
        if receipt:
            break_err(receipt)
        regress = await reconcile(maker, key, Rpc(intent.genesis_hash, receipt))
        assert json.loads(regress[1])["verdict"] == "replayed"
    assert await rows(maker) == 1


async def test_reconcile_rpc_outside_transaction(prepared):
    maker, key, intent, wire = prepared
    gate = threading.Event()
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire), gate)
    task = asyncio.create_task(reconcile(maker, key, rpc))
    assert await asyncio.to_thread(rpc.started.wait, 10)
    async with maker.begin() as db:
        assert await PayoutDeliveryRepository(db).lock_intent(
            ClaimKey(intent_id=UUID(key["intent_id"]))
        )
    gate.set()
    assert json.loads((await task)[1])["verdict"] == "confirmed"


async def test_reconcile_db_failure_is_never_success(prepared):
    maker, key, intent, wire = prepared
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire))
    async with maker.begin() as holder:
        await PayoutDeliveryRepository(holder).lock_intent(
            ClaimKey(intent_id=UUID(key["intent_id"]))
        )
        code, out = await reconcile(maker, key, rpc)
    assert (code, json.loads(out)) == (9, {"verdict": "busy"})
    assert await rows(maker) == 0


async def test_confirmation_direct_sql_guards(prepared):
    maker, key, intent, wire = prepared
    insert = (
        "INSERT INTO payout_delivery_confirmations(intent_id, finalized_slot, "
        "verifier_version, evidence_digest) VALUES (:i, :s, :v, :d)"
    )
    good = {"i": key["intent_id"], "s": "7", "v": 1, "d": "a" * 64}
    bad = [
        ({"i": "00000000-0000-4000-8000-000000000000"}, "23503"),
        ({"s": None}, "23502"),
        ({"s": "18446744073709551616"}, "23514"),
        ({"s": "-1"}, "23514"),
        ({"v": 0}, "23514"),
        ({"d": "A" * 64}, "23514"),
    ]
    for override, state in bad:
        with pytest.raises(DBAPIError) as error:
            async with maker.begin() as db:
                await db.execute(text(insert), good | override)
        assert error.value.orig.sqlstate == state
    async with maker.begin() as db:
        await db.execute(text(insert), good)
    for sql, state in (
        (insert, "23505"),
        ("UPDATE payout_delivery_confirmations SET verifier_version = 2", "23514"),
        ("DELETE FROM payout_delivery_confirmations", "23514"),
        ("TRUNCATE payout_delivery_confirmations", "23514"),
    ):
        with pytest.raises(DBAPIError) as error:
            async with maker.begin() as db:
                await db.execute(text(sql), good if sql == insert else {})
        assert error.value.orig.sqlstate == state


async def test_prepared_expiry_and_old_commands_unchanged(prepared):
    maker, key, intent, wire = prepared
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire))
    await reconcile(maker, key, rpc)
    async with maker() as db:
        assert await db.scalar(text("SELECT state FROM payout_delivery_claims")) == "prepared"
    show_attempt = await attempts.leases.invoke_claim(
        maker, "attempt-show", {"intent_id": key["intent_id"]}
    )
    assert show_attempt[1]["state"] == "prepared"
    claim = await attempts.leases.invoke_claim(maker, "claim", key | {"ttl_seconds": 1})
    assert claim == (11, {"verdict": "unavailable"})


async def test_reconcile_never_signs_sends_or_credits_and_redacts(prepared):
    maker, key, intent, wire = prepared
    rpc = Rpc(intent.genesis_hash, build_receipt(intent, wire))
    outputs = [(await reconcile(maker, key, rpc))[1], json.dumps((await show(maker, key))[1])]
    assert set(rpc.methods) <= {"getGenesisHash", "getTransaction"}
    for out in outputs:
        assert set(json.loads(out)) <= {
            "verdict",
            "intent_id",
            "state",
            "transaction_digest",
            "finalized_slot",
            "verifier_version",
        }
        for secret in (ENDPOINT, base64.b64encode(wire).decode(), intent.source, intent.mint):
            assert secret not in out
    out = io.StringIO()
    await run_reconcile(
        io.StringIO(
            json.dumps(
                {
                    "intent_id": key["intent_id"],
                    "trusted_genesis_hash": str(Hash.default()),
                    "rpc_endpoint": "http://user:pw@rpc.invalid",
                }
            )
        ),
        out,
        maker,
        rpc.transport,
    )
    assert json.loads(out.getvalue()) == {"verdict": "invalid_input"}
