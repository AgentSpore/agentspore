"""Prove attempt-send broadcasts the stored wire unchanged, using synthetic keys and PostgreSQL."""

import asyncio
import base64
import io
import json
import threading
from uuid import uuid4

import httpx
import pytest
import test_payout_reconcile_db as reconciled
from solders.hash import Hash
from solders.transaction import Transaction

from app.cli.payout_delivery import run_reconcile

attempt_db = reconciled.attempt_db
claims_db = reconciled.claims_db
delivery_db = reconciled.delivery_db
marketplace_pg = reconciled.marketplace_pg
signed_envelope = reconciled.signed_envelope
native_case = reconciled.native_case
allocation = reconciled.allocation
prepared = reconciled.prepared

PREFLIGHT = -32002


def rpc_error(code, message, err=None):
    data = {"err": err} if err is not None else None
    return {"code": code, "message": message, "data": data}


def mint_account(intent, owner=None, decimals=None, extensions=(), kind="mint"):
    info = {"decimals": intent.decimals if decimals is None else decimals}
    if extensions is not None:
        info["extensions"] = [{"extension": name, "state": {}} for name in extensions]
    parsed = {"type": kind, "info": info}
    value = {"owner": owner or intent.program, "data": {"parsed": parsed, "program": "x"}}
    return {"context": {"slot": 1}, "value": value}


class SendRpc:
    """Serve genesis, mint and sendTransaction at the HTTP boundary; record every request."""

    def __init__(self, genesis, send_result=None, send_error=None, fail=None, gate=None, mint=None):
        self.genesis, self.send_result, self.send_error = genesis, send_result, send_error
        self.fail, self.gate, self.mint = fail, gate, mint
        self.raw = None
        self.requests: list[dict] = []
        self.started = threading.Event()

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        self.started.set()
        if self.gate is not None:
            self.gate.wait(10)
        if body["method"] == "getGenesisHash":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": self.genesis})
        if body["method"] == "getAccountInfo":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": self.mint})
        if self.fail is not None:
            raise self.fail
        if self.raw is not None:
            return httpx.Response(200, json=self.raw)
        envelope = {"jsonrpc": "2.0", "id": 1}
        if self.send_error is not None:
            envelope["error"] = self.send_error
        else:
            envelope["result"] = self.send_result
        return httpx.Response(200, json=envelope)

    @property
    def methods(self):
        return [item["method"] for item in self.requests]

    @property
    def transport(self):
        return httpx.MockTransport(self)


def first_signature(wire):
    return str(Transaction.from_bytes(wire).signatures[0])


async def send(maker, key, rpc, **override):
    payload = {
        "intent_id": key["intent_id"],
        "trusted_genesis_hash": str(Hash.default()),
        "rpc_endpoint": reconciled.ENDPOINT,
    } | override
    out = io.StringIO()
    code = await run_reconcile(
        io.StringIO(json.dumps(payload)), out, maker, rpc.transport, command="attempt-send"
    )
    return code, json.loads(out.getvalue())


async def test_send_broadcasts_exact_stored_wire_and_writes_nothing(prepared):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent), send_result=first_signature(wire))
    code, out = await send(maker, key, rpc)
    assert (code, out) == (0, {"verdict": "sent"})
    assert rpc.methods == ["getGenesisHash", "getAccountInfo", "sendTransaction"]
    wire_param, options = rpc.requests[2]["params"]
    assert base64.b64decode(wire_param) == wire
    assert options == {
        "encoding": "base64",
        "skipPreflight": False,
        "preflightCommitment": "finalized",
        "maxRetries": 0,
    }
    assert await reconciled.rows(maker) == 0


async def test_send_genesis_mismatch_never_sends(prepared):
    maker, key, intent, wire = prepared
    rpc = SendRpc(
        str(Hash.new_unique()), mint=mint_account(intent), send_result=first_signature(wire)
    )
    assert await send(maker, key, rpc) == (3, {"verdict": "unsupported"})
    assert rpc.methods == ["getGenesisHash"]


async def test_send_untrusted_genesis_makes_no_request(prepared):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent), send_result=first_signature(wire))
    other = str(Hash.new_unique())
    assert await send(maker, key, rpc, trusted_genesis_hash=other) == (
        3,
        {"verdict": "unsupported"},
    )
    assert rpc.requests == []


async def test_send_signature_mismatch_is_unsupported(prepared):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent), send_result="1" * 64)
    assert await send(maker, key, rpc) == (3, {"verdict": "unsupported"})


@pytest.mark.parametrize(
    ("kwargs", "code", "verdict"),
    [
        ({"send_error": rpc_error(PREFLIGHT, "x", "AlreadyProcessed")}, 0, "already_processed"),
        (
            {"send_error": rpc_error(-32002, "This transaction has already been processed")},
            0,
            "already_processed",
        ),
        ({"send_error": rpc_error(PREFLIGHT, "x", "BlockhashNotFound")}, 2, "unresolved"),
        ({"send_error": rpc_error(-32005, "Node is unhealthy")}, 2, "unresolved"),
        (
            {"send_error": rpc_error(PREFLIGHT, "x", {"InstructionError": [0, "Custom"]})},
            13,
            "preflight_rejected",
        ),
        ({"send_result": 5}, 2, "unresolved"),
        ({"fail": httpx.ReadTimeout("slow")}, 2, "unresolved"),
        ({"fail": httpx.ConnectError("down")}, 2, "unresolved"),
    ],
)
async def test_send_rpc_outcomes(prepared, kwargs, code, verdict):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent), **kwargs)
    assert await send(maker, key, rpc) == (code, {"verdict": verdict})
    assert await reconciled.rows(maker) == 0


@pytest.mark.parametrize(
    "raw",
    [
        {"jsonrpc": "2.0", "id": 1, "error": "boom"},
        {"jsonrpc": "2.0", "id": 1, "error": ["x"]},
        {"jsonrpc": "2.0", "id": 1, "error": 7},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": 1, "data": "text"}},
        {"jsonrpc": "2.0", "id": 1, "error": None},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 2, "result": "x"},
        ["not", "an", "object"],
        "text",
        None,
    ],
)
async def test_send_malformed_envelope_is_unresolved(prepared, raw):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent))
    rpc.raw = raw
    assert await send(maker, key, rpc) == (2, {"verdict": "unresolved"})


@pytest.mark.parametrize(
    "extensions", [(), ("metadataPointer",), ("metadataPointer", "tokenMetadata")]
)
async def test_send_allows_allowlisted_mint_extensions(prepared, extensions):
    maker, key, intent, wire = prepared
    mint = mint_account(intent, extensions=extensions)
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire), mint=mint)
    assert await send(maker, key, rpc) == (0, {"verdict": "sent"})


@pytest.mark.parametrize(
    "bad",
    [
        {"extensions": ("transferFeeConfig",)},
        {"extensions": ("metadataPointer", "transferHook")},
        {"extensions": ("confidentialTransferMint",)},
        {"extensions": ("nonTransferable",)},
        {"extensions": ("pausableConfig",)},
        {"extensions": ("someFutureExtension",)},
        {"owner": "11111111111111111111111111111111"},
        {"decimals": 99},
        {"kind": "account"},
    ],
)
async def test_send_refuses_unsupported_mint_without_sending(prepared, bad):
    maker, key, intent, wire = prepared
    rpc = SendRpc(
        intent.genesis_hash, send_result=first_signature(wire), mint=mint_account(intent, **bad)
    )
    assert await send(maker, key, rpc) == (3, {"verdict": "unsupported"})
    assert "sendTransaction" not in rpc.methods


@pytest.mark.parametrize(
    "mint",
    [None, {"value": None}, {"value": "x"}, {"value": {"owner": "x"}}, "junk"],
)
async def test_send_malformed_mint_is_unresolved_without_sending(prepared, mint):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire), mint=mint)
    assert await send(maker, key, rpc) == (2, {"verdict": "unresolved"})
    assert "sendTransaction" not in rpc.methods


async def test_send_refused_after_confirmation_without_rpc(prepared):
    maker, key, intent, wire = prepared
    rpc = reconciled.Rpc(intent.genesis_hash, reconciled.build_receipt(intent, wire))
    assert json.loads((await reconciled.reconcile(maker, key, rpc))[1])["verdict"] == "confirmed"
    idle = SendRpc(
        intent.genesis_hash, mint=mint_account(intent), send_result=first_signature(wire)
    )
    assert await send(maker, key, idle) == (12, {"verdict": "already_confirmed"})
    assert idle.requests == []


async def test_send_without_attempt_is_not_found(prepared):
    maker, _, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent), send_result=first_signature(wire))
    assert await send(maker, {"intent_id": str(uuid4())}, rpc) == (6, {"verdict": "not_found"})
    assert rpc.requests == []


@pytest.mark.parametrize(
    "override",
    [
        {"rpc_endpoint": "http://rpc.invalid"},
        {"rpc_endpoint": "https://user:pw@rpc.invalid"},
        {"rpc_endpoint": "https://rpc.invalid/?api-key=x"},
        {"extra": 1},
    ],
)
async def test_send_rejects_bad_input_without_rpc(prepared, override):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, mint=mint_account(intent), send_result=first_signature(wire))
    assert await send(maker, key, rpc, **override) == (5, {"verdict": "invalid_input"})
    assert rpc.requests == []


async def test_send_holds_no_database_lock_during_rpc(prepared):
    maker, key, intent, wire = prepared
    gate = threading.Event()
    rpc = SendRpc(
        intent.genesis_hash, mint=mint_account(intent), send_result=first_signature(wire), gate=gate
    )
    task = asyncio.create_task(send(maker, key, rpc))
    assert await asyncio.to_thread(rpc.started.wait, 10)
    async with maker.begin() as db:
        repo = reconciled.PayoutDeliveryRepository(db)
        assert await repo.lock_intent(
            reconciled.ClaimKey(intent_id=reconciled.UUID(key["intent_id"]))
        )
    gate.set()
    assert (await task)[1] == {"verdict": "sent"}
