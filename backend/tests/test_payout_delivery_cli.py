"""Exercise the CLI with synthetic RPC receipts built from real SPL messages."""

import io
import json
import subprocess
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import base58
import httpx
import pytest
from solders.hash import Hash
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.token.associated import get_associated_token_address
from spl.token.constants import TOKEN_2022_PROGRAM_ID
from spl.token.instructions import TransferCheckedParams, transfer_checked

from app.cli.payout_delivery import run


@pytest.fixture(params=[False, True], ids=["distinct-payer", "shared-payer"])
def transfer_message(request):
    keys = [Pubkey.from_bytes(bytes([n]) * 32) for n in range(1, 7)]
    payer, authority, source, _, mint, recipient = keys
    destination = get_associated_token_address(recipient, mint, TOKEN_2022_PROGRAM_ID)
    if request.param:
        payer = authority
    instruction = transfer_checked(
        TransferCheckedParams(TOKEN_2022_PROGRAM_ID, source, mint, destination, authority, 10, 6)
    )
    message = Message.new_with_blockhash([instruction], payer, Hash.default())
    intent: dict = {
        name: str(value)
        for name, value in zip(
            ("fee_payer", "authority", "source", "destination", "mint", "recipient"),
            (payer, authority, source, destination, mint, recipient),
        )
    }
    intent.update(
        program=str(TOKEN_2022_PROGRAM_ID),
        genesis_hash=str(Hash.default()),
        decimals=6,
        raw_amount="10",
        signature=str(Signature.default()),
        rpc_endpoint="http://rpc.invalid",
    )
    return intent, message


@pytest.fixture
def receipt_case(transfer_message):
    intent, message = transfer_message
    source, destination, mint, authority, recipient = (
        Pubkey.from_string(intent[name])
        for name in ("source", "destination", "mint", "authority", "recipient")
    )
    balances = [
        {
            "accountIndex": message.account_keys.index(account),
            "mint": str(mint),
            "programId": str(TOKEN_2022_PROGRAM_ID),
            "owner": str(owner),
            "uiTokenAmount": {"amount": amount, "decimals": 6},
        }
        for account, owner, amount in [(source, authority, "20"), (destination, recipient, "0")]
    ]
    receipt = {
        "version": "legacy",
        "meta": {
            "err": None,
            "fee": 5,
            "innerInstructions": [],
            "preBalances": [20] * len(message.account_keys),
            "postBalances": [15] + [20] * (len(message.account_keys) - 1),
            "preTokenBalances": balances,
            "postTokenBalances": json.loads(json.dumps(balances)),
        },
        "transaction": {
            "signatures": [str(Signature.default())] * message.header.num_required_signatures,
            "message": {},
        },
    }
    # solders serializes bytes as arrays; RPC json encoding uses base58 strings.
    receipt["transaction"]["message"] = {
        "accountKeys": list(map(str, message.account_keys)),
        "header": {
            "numRequiredSignatures": message.header.num_required_signatures,
            "numReadonlySignedAccounts": message.header.num_readonly_signed_accounts,
            "numReadonlyUnsignedAccounts": message.header.num_readonly_unsigned_accounts,
        },
        "recentBlockhash": str(message.recent_blockhash),
        "instructions": [
            {
                "programIdIndex": message.instructions[0].program_id_index,
                "accounts": list(message.instructions[0].accounts),
                "data": base58.b58encode(message.instructions[0].data).decode(),
            }
        ],
    }
    for balance in receipt["meta"]["postTokenBalances"]:
        balance["uiTokenAmount"]["amount"] = "10"
    return intent, receipt


def negotiated_receipt(body, receipt):
    """Follow Agave's legacy version-field negotiation at the RPC boundary."""
    if "maxSupportedTransactionVersion" in body["params"][1]:
        return receipt
    return {key: value for key, value in receipt.items() if key != "version"}


def rpc_response(request, case, calls):
    """Serve synthetic JSON-RPC responses at the external HTTP boundary."""
    intent, receipt, value = case
    body = json.loads(request.content)
    calls.append(body)
    if value == "timeout":
        raise httpx.ReadTimeout("synthetic-private-marker")
    if value == "rpc_error":
        return httpx.Response(200, json={"error": "private"})
    if value == "malformed":
        return httpx.Response(200, json=[])
    result = (
        negotiated_receipt(body, receipt)
        if body["method"] == "getTransaction"
        else intent["genesis_hash"]
    )
    if value == "null" and body["method"] == "getTransaction":
        result = None
    if value == "network" and body["method"] == "getGenesisHash":
        result = intent["mint"]
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


@pytest.mark.parametrize(
    "path,value,expected",
    [
        ("", "matching", "matching"),
        ("", "null", "unresolved"),
        ("", "timeout", "unresolved"),
        ("", "rpc_error", "unresolved"),
        ("", "malformed", "unresolved"),
        ("", "network", "unsupported"),
        ("transaction.signatures.0", str(Signature.from_bytes(bytes([9]) * 64)), "unsupported"),
        ("transaction.message.instructions.0.programIdIndex", 0, "unsupported"),
        ("meta.postTokenBalances.0.owner", str(Pubkey.default()), "unsupported"),
        ("meta.postTokenBalances.0.mint", str(Pubkey.default()), "unsupported"),
        ("meta.postTokenBalances.0.programId", str(Pubkey.default()), "unsupported"),
        ("meta.postTokenBalances.1.uiTokenAmount.amount", "9", "unsupported"),
        ("meta.innerInstructions", [{"index": 0, "instructions": []}], "unsupported"),
        ("transaction.message.instructions", [], "unresolved"),
        ("transaction.message.header.numRequiredSignatures", 0, "unresolved"),
        ("meta.postTokenBalances.1.accountIndex", -1, "unresolved"),
        ("meta.innerInstructions", None, "unsupported"),
        ("meta.err", {"InstructionError": [0, "InvalidArgument"]}, "failed"),
        ("meta.postTokenBalances.1", "duplicate", "unsupported"),
        ("transaction.message.instructions", [{}, {}], "unresolved"),
        ("meta.preTokenBalances.0.uiTokenAmount.amount", "19", "unsupported"),
        ("transaction.message.header.numRequiredSignatures", True, "unresolved"),
        ("transaction.message.header.numReadonlySignedAccounts", -1, "unresolved"),
        ("transaction.message.header.numReadonlyUnsignedAccounts", 256, "unresolved"),
        ("transaction.message.header.numRequiredSignatures", 256, "unresolved"),
        ("transaction.message.header.numReadonlySignedAccounts", 3, "unresolved"),
        ("transaction.message.header.numReadonlyUnsignedAccounts", 6, "unresolved"),
        ("transaction.message.instructions.0.data", [], "unresolved"),
        ("transaction.message.instructions.0.data", {}, "unresolved"),
        ("transaction.message.recentBlockhash", "bad", "unresolved"),
        ("version", 0, "unsupported"),
        ("meta.fee", True, "unresolved"),
    ],
)
def test_cli_rpc_boundary(receipt_case, path, value, expected):
    intent, receipt = receipt_case
    if path:
        target = receipt
        parts = [int(part) if part.isdecimal() else part for part in path.split(".")]
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = target[0] if value == "duplicate" else value
    calls = []

    output = io.StringIO()
    code = run(
        io.StringIO(json.dumps(intent)),
        output,
        httpx.MockTransport(partial(rpc_response, case=(intent, receipt, value), calls=calls)),
    )
    assert json.loads(output.getvalue()) == {"verdict": expected}
    assert code == {"matching": 0, "unresolved": 2, "unsupported": 3, "failed": 4}[expected]
    if expected == "matching":
        assert calls[0]["method"] == "getGenesisHash"
        assert calls[1]["params"][1] == {
            "commitment": "finalized",
            "encoding": "json",
            "maxSupportedTransactionVersion": 0,
        }


@pytest.mark.parametrize("amount", ["0", "-1", "1.1", "01", "18446744073709551616", 1, True])
def test_invalid_input(receipt_case, amount):
    intent, _ = receipt_case
    intent["raw_amount"] = amount
    output = io.StringIO()
    assert run(io.StringIO(json.dumps(intent)), output) == 5
    assert json.loads(output.getvalue()) == {"verdict": "invalid_input"}


def test_subprocess_loopback_and_private_input(receipt_case):
    intent, receipt = receipt_case

    class RPC(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            result = (
                intent["genesis_hash"]
                if body["method"] == "getGenesisHash"
                else negotiated_receipt(body, receipt)
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode())

        def log_message(self, format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), RPC) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            intent["rpc_endpoint"] = "http://127.0.0.1:" + str(server.server_port)
            for field, value, code, verdict in [
                ("raw_amount", "10", 0, "matching"),
                ("raw_amount", "10", 2, "unresolved"),
                ("raw_amount", "private-input-marker", 5, "invalid_input"),
                ("genesis_hash", "bad-genesis-marker", 5, "invalid_input"),
            ]:
                if code == 2:
                    receipt["transaction"]["message"]["instructions"][0]["data"] = []
                intent["raw_amount"] = "10"
                intent[field] = value
                result = subprocess.run(
                    [sys.executable, "-m", "app.cli.payout_delivery"],
                    input=json.dumps(intent),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert (result.returncode, json.loads(result.stdout), result.stderr) == (
                    code,
                    {"verdict": verdict},
                    "",
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize(
    "path",
    [
        "meta",
        "meta.err",
        "meta.fee",
        "meta.innerInstructions",
        "meta.preBalances",
        "meta.postBalances",
        "meta.preTokenBalances",
        "meta.postTokenBalances",
        "meta.preTokenBalances.0.owner",
        "meta.preTokenBalances.0.programId",
        "meta.postTokenBalances.1.uiTokenAmount.amount",
        "transaction.signatures",
        "transaction.message.recentBlockhash",
        "transaction.message.header",
        "transaction.message.accountKeys",
        "version",
    ],
)
def test_missing_evidence(receipt_case, path):
    intent, receipt = receipt_case
    parts = [int(part) if part.isdecimal() else part for part in path.split(".")]
    target = receipt
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": intent["genesis_hash"]
                if json.loads(request.content)["method"] == "getGenesisHash"
                else receipt,
            },
        )
    )
    output = io.StringIO()
    assert run(io.StringIO(json.dumps(intent)), output, transport) == 2
    assert json.loads(output.getvalue()) == {"verdict": "unresolved"}


@pytest.mark.parametrize(
    "field",
    [
        "genesis_hash",
        "mint",
        "program",
        "source",
        "destination",
        "authority",
        "fee_payer",
        "recipient",
        "decimals",
        "raw_amount",
        "signature",
        "rpc_endpoint",
    ],
)
def test_missing_input(receipt_case, field):
    intent, _ = receipt_case
    del intent[field]
    output = io.StringIO()
    assert run(io.StringIO(json.dumps(intent)), output) == 5
    assert json.loads(output.getvalue()) == {"verdict": "invalid_input"}


def test_invalid_genesis(receipt_case):
    intent, _ = receipt_case
    intent["genesis_hash"] = "bad-genesis-marker"
    output = io.StringIO()
    assert run(io.StringIO(json.dumps(intent)), output) == 5
    assert json.loads(output.getvalue()) == {"verdict": "invalid_input"}
