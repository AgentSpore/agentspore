"""Prove the block-height command: genesis-bound, finalized, u64-checked, endpoint-private."""

import io
import json
from functools import partial
from types import SimpleNamespace

import httpx
import pytest
from solders.hash import Hash

from app.cli import payout_delivery as cli

ENDPOINT = "https://rpc-private.invalid/secret-path-marker"
GENESIS = str(Hash.new_unique())
KEY = str(Hash.new_unique())


class HeightRpc:
    """JSON-RPC double answering getGenesisHash and getBlockHeight."""

    def __init__(self, height=777, genesis=GENESIS, error=False):
        self.height, self.genesis, self.error = height, genesis, error
        self.requests = []
        self.transport = httpx.MockTransport(self)

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.error:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -1}})
        result = self.genesis if body["method"] == "getGenesisHash" else self.height
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


@pytest.fixture
def configure(monkeypatch, tmp_path):
    def apply(path=None):
        if path is None:
            path = tmp_path / "network.json"
            path.write_text(
                json.dumps(
                    {
                        "rpc_endpoint": ENDPOINT,
                        "trusted_genesis_hash": GENESIS,
                        "signer_pubkey": KEY,
                        "source_token_account": KEY,
                    }
                )
            )
            path.chmod(0o600)
        monkeypatch.setattr(
            cli, "get_settings", lambda: SimpleNamespace(payout_network_config_path=str(path))
        )

    return apply


def run(rpc, stdin="{}"):
    out = io.StringIO()
    code = cli.run_block_height(io.StringIO(stdin), out, rpc.transport)
    return code, out.getvalue()


def test_found_uses_finalized_after_genesis_check(configure, capsys):
    configure()
    rpc = HeightRpc(height=777)
    code, text = run(rpc)
    assert (code, json.loads(text)) == (0, {"verdict": "found", "height": 777})
    assert [r["method"] for r in rpc.requests] == ["getGenesisHash", "getBlockHeight"]
    assert rpc.requests[1]["params"] == [{"commitment": "finalized"}]
    captured = capsys.readouterr()
    assert "secret-path-marker" not in text + captured.out + captured.err


def test_genesis_mismatch_is_unsupported(configure):
    configure()
    rpc = HeightRpc(genesis=str(Hash.new_unique()))
    code, text = run(rpc)
    assert (code, json.loads(text)) == (3, {"verdict": "unsupported"})
    assert [r["method"] for r in rpc.requests] == ["getGenesisHash"]


def test_rpc_error_envelope_is_unresolved(configure):
    configure()
    code, text = run(HeightRpc(error=True))
    assert (code, json.loads(text)) == (2, {"verdict": "unresolved"})
    assert "secret-path-marker" not in text


@pytest.mark.parametrize("height", [True, -1, "5", 2**64, 1.5, None])
def test_malformed_height_is_unresolved(configure, height):
    configure()
    code, text = run(HeightRpc(height=height))
    assert (code, json.loads(text)) == (2, {"verdict": "unresolved"})


def test_u64_maximum_is_accepted(configure):
    configure()
    code, text = run(HeightRpc(height=2**64 - 1))
    assert (code, json.loads(text)) == (0, {"verdict": "found", "height": 2**64 - 1})


def test_missing_config_is_configuration_error(configure, tmp_path):
    configure(tmp_path / "missing.json")
    rpc = HeightRpc()
    code, text = run(rpc)
    assert (code, json.loads(text)) == (14, {"verdict": "configuration_error"})
    assert rpc.requests == []


@pytest.mark.parametrize("stdin", ['{"x": 1}', "", "[]", "null"])
def test_stdin_must_be_empty_object(configure, stdin):
    configure()
    rpc = HeightRpc()
    code, text = run(rpc, stdin)
    assert (code, json.loads(text)) == (5, {"verdict": "invalid_input"})
    assert rpc.requests == []


def test_main_routes_block_height(configure, monkeypatch):
    configure()
    rpc = HeightRpc(height=9)
    monkeypatch.setattr(
        cli, "run_block_height", partial(cli.run_block_height, transport=rpc.transport)
    )
    out = io.StringIO()
    assert cli.main(["block-height"], io.StringIO("{}"), out) == 0
    assert json.loads(out.getvalue()) == {"verdict": "found", "height": 9}
