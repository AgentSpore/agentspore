"""Prove the server-config-driven auto-* payout commands on PostgreSQL with injected HTTP."""

import base64
import io
import json
import stat
from types import SimpleNamespace

import httpx
import pytest
import test_payout_reconcile_db as reconciled
import test_payout_send_db as sent
from solders.hash import Hash

from app.cli import payout_delivery as cli

attempt_db = reconciled.attempt_db
claims_db = reconciled.claims_db
delivery_db = reconciled.delivery_db
marketplace_pg = reconciled.marketplace_pg
signed_envelope = reconciled.signed_envelope
native_case = reconciled.native_case
allocation = reconciled.allocation
prepared = reconciled.prepared

ENDPOINT = "https://rpc-private.invalid/secret-path-marker"
BLOCKHASH = str(Hash.new_unique())


class AutoRpc(sent.SendRpc):
    """Add getLatestBlockhash to the send RPC double."""

    def __call__(self, request):
        body = json.loads(request.content)
        if body["method"] == "getLatestBlockhash":
            self.requests.append(body)
            value = {"blockhash": BLOCKHASH, "lastValidBlockHeight": 1234}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": value}})
        return super().__call__(request)


def write_config(tmp_path, intent, mode=0o600, **override):
    config = {
        "rpc_endpoint": ENDPOINT,
        "trusted_genesis_hash": intent.genesis_hash,
        "signer_pubkey": intent.authority,
        "source_token_account": intent.source,
    } | override
    path = tmp_path / "network.json"
    path.write_text(json.dumps(config))
    path.chmod(mode)
    return path


@pytest.fixture
def configure(monkeypatch, tmp_path):
    def apply(intent, path=None, **kwargs):
        path = path if path is not None else write_config(tmp_path, intent, **kwargs)
        monkeypatch.setattr(
            cli, "get_settings", lambda: SimpleNamespace(payout_network_config_path=str(path))
        )
        return path

    return apply


async def compile_auto(maker, fence, rpc, output):
    out = io.StringIO()
    code = await cli.run_auto_compile(
        io.StringIO(json.dumps(fence)), out, str(output), maker, rpc.transport
    )
    return code, out.getvalue()


async def auto(command, maker, key, rpc):
    out = io.StringIO()
    payload = io.StringIO(json.dumps({"intent_id": key["intent_id"]}))
    code = await cli.run_auto(command, payload, out, maker, rpc.transport)
    return code, json.loads(out.getvalue())


@pytest.fixture
def fence(signed_envelope):
    return {
        "intent_id": str(signed_envelope.intent_id),
        "worker_id": str(signed_envelope.worker_id),
        "epoch": 1,
    }


@pytest.mark.parametrize("case", ["empty", "missing", "unknown_key", "loose_mode"])
async def test_bad_config_is_configuration_error_and_echoes_nothing(
    case, attempt_db, signed_envelope, native_case, configure, tmp_path, fence, capsys
):
    maker = attempt_db[0]
    intent, _ = native_case
    path = {
        "empty": "",
        "missing": tmp_path / "missing.json",
        "unknown_key": write_config(tmp_path, intent, extra_marker="x"),
        "loose_mode": write_config(tmp_path, intent, mode=0o640),
    }[case]
    configure(intent, path=path)
    rpc = AutoRpc(intent.genesis_hash)
    output = tmp_path / "request.json"
    code, text_out = await compile_auto(maker, fence, rpc, output)
    assert (code, json.loads(text_out)) == (14, {"verdict": "configuration_error"})
    assert not output.exists() and rpc.requests == []
    for command in ("auto-send", "auto-reconcile"):
        assert await auto(command, maker, fence, rpc) == (14, {"verdict": "configuration_error"})
    captured = capsys.readouterr()
    assert "secret-path-marker" not in captured.out + captured.err + text_out


async def test_auto_compile_writes_exclusive_private_request(
    attempt_db, signed_envelope, native_case, configure, tmp_path, fence, capsys
):
    maker = attempt_db[0]
    intent, _ = native_case
    configure(intent)
    rpc = AutoRpc(intent.genesis_hash)
    output = tmp_path / "request.json"
    code, text_out = await compile_auto(maker, fence, rpc, output)
    assert code == 0 and json.loads(text_out)["verdict"] == "compiled"
    assert rpc.methods == ["getGenesisHash", "getLatestBlockhash"]
    artifact = json.loads(output.read_text())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert artifact["recent_blockhash"] == BLOCKHASH
    assert artifact["last_valid_block_height"] == "1234"
    assert artifact["genesis_hash"] == intent.genesis_hash
    assert (await compile_auto(maker, fence, rpc, output))[0] == 5
    assert json.loads(output.read_text()) == artifact
    captured = capsys.readouterr()
    assert "secret-path-marker" not in captured.out + captured.err + text_out


async def test_auto_compile_genesis_mismatch_is_unsupported(
    attempt_db, signed_envelope, native_case, configure, tmp_path, fence
):
    intent, _ = native_case
    configure(intent)
    rpc = AutoRpc(str(Hash.new_unique()))
    output = tmp_path / "request.json"
    code, text_out = await compile_auto(attempt_db[0], fence, rpc, output)
    assert (code, json.loads(text_out)) == (3, {"verdict": "unsupported"})
    assert not output.exists() and rpc.methods == ["getGenesisHash"]


@pytest.mark.parametrize("override", ["signer_pubkey", "source_token_account"])
async def test_auto_compile_account_mismatch_is_unsupported(
    override, attempt_db, signed_envelope, native_case, configure, tmp_path, fence
):
    intent, _ = native_case
    configure(intent, **{override: str(Hash.new_unique())})
    rpc = AutoRpc(intent.genesis_hash)
    output = tmp_path / "request.json"
    code, text_out = await compile_auto(attempt_db[0], fence, rpc, output)
    assert (code, json.loads(text_out)) == (3, {"verdict": "unsupported"})
    assert not output.exists() and rpc.requests == []


async def test_auto_compile_stale_fence_writes_no_file(
    attempt_db, signed_envelope, native_case, configure, tmp_path, fence
):
    intent, _ = native_case
    configure(intent)
    rpc = AutoRpc(intent.genesis_hash)
    output = tmp_path / "request.json"
    code, text_out = await compile_auto(attempt_db[0], fence | {"epoch": 2}, rpc, output)
    assert (code, json.loads(text_out)) == (10, {"verdict": "stale"})
    assert not output.exists()


async def test_auto_send_broadcasts_exact_stored_wire(prepared, configure):
    maker, key, intent, wire = prepared
    configure(intent)
    rpc = AutoRpc(
        intent.genesis_hash, mint=sent.mint_account(intent), send_result=sent.first_signature(wire)
    )
    assert await auto("auto-send", maker, key, rpc) == (0, {"verdict": "sent"})
    assert base64.b64decode(rpc.requests[-1]["params"][0]) == wire


async def test_auto_reconcile_stores_confirmation(prepared, configure):
    maker, key, intent, wire = prepared
    configure(intent)
    rpc = reconciled.Rpc(intent.genesis_hash, reconciled.build_receipt(intent, wire))
    code, body = await auto("auto-reconcile", maker, key, rpc)
    assert code == 0 and body["verdict"] == "confirmed"
    assert await reconciled.rows(maker) == 1


async def pending(maker, limit=10):
    out = io.StringIO()
    code = await cli.run_pending(out, limit, maker)
    return code, json.loads(out.getvalue())


async def phase(maker, key):
    code, body = await pending(maker)
    assert code == 0 and body["verdict"] == "found"
    [item] = body["intents"]
    assert item["intent_id"] == key["intent_id"] and set(item) == {"intent_id", "phase"}
    return item["phase"]


@pytest.fixture
async def confirmations_db(attempt_db):
    async with attempt_db[0].begin() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute(
            (reconciled.MIGRATIONS / "V117__payout_delivery_confirmations.sql").read_text()
        )
    return attempt_db


async def test_pending_unclaimed_then_claimed(confirmations_db):
    maker, _, _, key = confirmations_db
    assert await phase(maker, key) == "unclaimed"
    claim = key | {"ttl_seconds": 300}
    assert (await reconciled.attempts.leases.invoke_claim(maker, "claim", claim))[0] == 0
    assert await phase(maker, key) == "claimed"


async def test_pending_prepared_then_confirmed(prepared, configure):
    maker, key, intent, wire = prepared
    configure(intent)
    assert await phase(maker, key) == "prepared"
    rpc = reconciled.Rpc(intent.genesis_hash, reconciled.build_receipt(intent, wire))
    await auto("auto-reconcile", maker, key, rpc)
    assert await phase(maker, key) == "confirmed"


@pytest.mark.parametrize("limit", [0, 1001, -1])
async def test_pending_rejects_bad_limit(confirmations_db, limit):
    assert (await pending(confirmations_db[0], limit))[0] == 5
