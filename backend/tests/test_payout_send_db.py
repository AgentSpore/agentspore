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


class SendRpc:
    """Serve genesis and sendTransaction at the HTTP boundary; record every request."""

    def __init__(self, genesis, send_result=None, send_error=None, fail=None, gate=None):
        self.genesis, self.send_result, self.send_error = genesis, send_result, send_error
        self.fail, self.gate = fail, gate
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
        if self.fail is not None:
            raise self.fail
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
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire))
    code, out = await send(maker, key, rpc)
    assert (code, out) == (0, {"verdict": "sent"})
    assert rpc.methods == ["getGenesisHash", "sendTransaction"]
    wire_param, options = rpc.requests[1]["params"]
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
    rpc = SendRpc(str(Hash.new_unique()), send_result=first_signature(wire))
    assert await send(maker, key, rpc) == (3, {"verdict": "unsupported"})
    assert rpc.methods == ["getGenesisHash"]


async def test_send_untrusted_genesis_makes_no_request(prepared):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire))
    other = str(Hash.new_unique())
    assert await send(maker, key, rpc, trusted_genesis_hash=other) == (
        3,
        {"verdict": "unsupported"},
    )
    assert rpc.requests == []


async def test_send_signature_mismatch_is_unsupported(prepared):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, send_result="1" * 64)
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
            4,
            "failed",
        ),
        ({"send_result": 5}, 2, "unresolved"),
        ({"fail": httpx.ReadTimeout("slow")}, 2, "unresolved"),
        ({"fail": httpx.ConnectError("down")}, 2, "unresolved"),
    ],
)
async def test_send_rpc_outcomes(prepared, kwargs, code, verdict):
    maker, key, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, **kwargs)
    assert await send(maker, key, rpc) == (code, {"verdict": verdict})
    assert await reconciled.rows(maker) == 0


async def test_send_refused_after_confirmation_without_rpc(prepared):
    maker, key, intent, wire = prepared
    rpc = reconciled.Rpc(intent.genesis_hash, reconciled.build_receipt(intent, wire))
    assert json.loads((await reconciled.reconcile(maker, key, rpc))[1])["verdict"] == "confirmed"
    idle = SendRpc(intent.genesis_hash, send_result=first_signature(wire))
    assert await send(maker, key, idle) == (12, {"verdict": "already_confirmed"})
    assert idle.requests == []


async def test_send_without_attempt_is_not_found(prepared):
    maker, _, intent, wire = prepared
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire))
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
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire))
    assert await send(maker, key, rpc, **override) == (5, {"verdict": "invalid_input"})
    assert rpc.requests == []


async def test_send_holds_no_database_lock_during_rpc(prepared):
    maker, key, intent, wire = prepared
    gate = threading.Event()
    rpc = SendRpc(intent.genesis_hash, send_result=first_signature(wire), gate=gate)
    task = asyncio.create_task(send(maker, key, rpc))
    assert await asyncio.to_thread(rpc.started.wait, 10)
    async with maker.begin() as db:
        repo = reconciled.PayoutDeliveryRepository(db)
        assert await repo.lock_intent(
            reconciled.ClaimKey(intent_id=reconciled.UUID(key["intent_id"]))
        )
    gate.set()
    assert (await task)[1] == {"verdict": "sent"}
