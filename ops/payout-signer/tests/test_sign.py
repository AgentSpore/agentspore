import base64
import json
import os
import stat

import pytest
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from payout_signer.signer import sign_request
from payout_signer.verdict import Verdict

from .conftest import LIMITS

DAY = 86400


def run(env, raw, name="r.json", out="out.json", now=1_000_000.0, digest=None):
    request = env.request(raw, name, digest)
    return sign_request(env.config, request, env.root / out, now=now)


def test_valid_transfer_signs_in_backend_shape(env):
    assert run(env, env.message()) is Verdict.SIGNED
    out = env.root / "out.json"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    body = json.loads(out.read_text())
    assert set(body) == {
        "intent_id",
        "worker_id",
        "epoch",
        "genesis_hash",
        "last_valid_block_height",
        "format_version",
        "signed_transaction_base64",
    }
    tx = VersionedTransaction.from_bytes(base64.b64decode(body["signed_transaction_base64"]))
    legacy = tx.into_legacy_transaction()
    assert legacy is not None
    legacy.sanitize()
    legacy.verify()
    assert legacy.message.account_keys[0] == env.signer.pubkey()


@pytest.mark.parametrize(
    "case",
    ["extra", "program", "mint", "decimals", "payer", "authority", "source", "zero", "garbage"],
)
def test_malformed_messages_rejected(env, case):
    other = Keypair().pubkey()
    kwargs = {
        "extra": {"extra": True},
        "program": {"program": other},
        "mint": {"mint": other},
        "decimals": {"decimals": 5},
        "payer": {"payer": other},
        "authority": {"authority": other},
        "source": {"source": other},
        "zero": {"amount": 0},
    }
    raw = b"\x00" * 40 if case == "garbage" else env.message(**kwargs[case])
    assert run(env, raw) is Verdict.REJECTED
    assert not (env.root / "out.json").exists()


def test_digest_mismatch_rejected(env):
    assert run(env, env.message(), digest="0" * 64) is Verdict.REJECTED


def test_request_with_unknown_field_rejected(env):
    path = env.request(env.message())
    body = json.loads(path.read_text()) | {"extra": 1}
    path.write_text(json.dumps(body))
    assert sign_request(env.config, path, env.root / "o.json", now=1.0) is Verdict.REJECTED


def test_per_transfer_boundary(env):
    assert run(env, env.message(LIMITS["max_amount_per_transfer"])) is Verdict.SIGNED
    over = env.message(LIMITS["max_amount_per_transfer"] + 1)
    assert run(env, over, "b.json", "o2.json") is Verdict.LIMIT_EXCEEDED


def test_per_recipient_boundary(env):
    dest = Keypair().pubkey()
    assert run(env, env.message(1000, dest), "a.json", "a.out") is Verdict.SIGNED
    assert run(env, env.message(500, dest), "b.json", "b.out") is Verdict.SIGNED
    assert run(env, env.message(1, dest), "c.json", "c.out") is Verdict.LIMIT_EXCEEDED


def test_total_boundary(env):
    for i in range(2):
        assert run(env, env.message(1000), f"{i}.json", f"{i}.out") is Verdict.SIGNED
    assert run(env, env.message(500), "e.json", "e.out") is Verdict.SIGNED
    assert run(env, env.message(1), "f.json", "f.out") is Verdict.LIMIT_EXCEEDED


def test_count_boundary(env):
    env.write_config(max_amount_total_24h=10**6, max_amount_per_recipient_24h=10**6)
    for i in range(LIMITS["max_transfers_24h"]):
        assert run(env, env.message(1), f"{i}.json", f"{i}.out") is Verdict.SIGNED
    assert run(env, env.message(1), "x.json", "x.out") is Verdict.LIMIT_EXCEEDED


def test_window_rolls(env):
    env.write_config(max_transfers_24h=1)
    assert run(env, env.message(1), "a.json", "a.out", now=1000.0) is Verdict.SIGNED
    late = run(env, env.message(2), "b.json", "b.out", now=1000.0 + DAY - 1)
    assert late is Verdict.LIMIT_EXCEEDED
    assert run(env, env.message(3), "c.json", "c.out", now=1000.0 + DAY + 1) is Verdict.SIGNED


def test_replay_identical_and_not_counted(env):
    env.write_config(max_transfers_24h=1)
    raw = env.message(7)
    assert run(env, raw, "a.json", "a.out") is Verdict.SIGNED
    assert run(env, raw, "b.json", "b.out") is Verdict.REPLAYED
    assert (env.root / "a.out").read_bytes() == (env.root / "b.out").read_bytes()
    assert run(env, env.message(8), "c.json", "c.out") is Verdict.LIMIT_EXCEEDED


def test_pause_file_blocks(env):
    env.pause.touch()
    assert run(env, env.message()) is Verdict.PAUSED
    assert not (env.root / "out.json").exists()


def test_output_never_overwritten_and_no_symlink(env):
    (env.root / "out.json").write_text("keep")
    assert run(env, env.message()) is Verdict.REJECTED
    assert (env.root / "out.json").read_text() == "keep"
    target = env.root / "target"
    os.symlink(target, env.root / "link.json")
    assert run(env, env.message(), "b.json", "link.json") is Verdict.REJECTED
    assert not target.exists()


def test_ledger_is_private(env):
    run(env, env.message())
    assert stat.S_IMODE((env.root / "ledger.sqlite").stat().st_mode) == 0o600


@pytest.mark.parametrize("key", list(LIMITS))
def test_missing_limit_is_config_error(env, key):
    env.write_config(**{key: None})
    assert run(env, env.message()) is Verdict.CONFIG_ERROR
