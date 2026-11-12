import json
import subprocess
import sys
import time

import pytest

from payout_signer.config import ConfigError, load_config
from payout_signer.ledger import Ledger

from .conftest import LIMITS


def cli(*args):
    return subprocess.run(  # noqa: S603 (fixed argv: own interpreter, tmp paths)
        [sys.executable, "-m", "payout_signer", *args], capture_output=True, text=True, check=False
    )


def test_status_numbers_only_and_pause_flag(env):
    with Ledger.locked(env.ledger) as ledger:
        ledger.record("d", "some-recipient", 5, now=time.time())
    env.pause.touch()
    done = cli("status", "--config", str(env.config))
    body = json.loads(done.stdout)
    assert done.returncode == 0
    assert body["paused"] is True
    assert body["transfers"] == {"used": 1, "limit": LIMITS["max_transfers_24h"]}
    assert body["total"] == {"used": 5, "limit": LIMITS["max_amount_total_24h"]}
    assert "some-recipient" not in done.stdout
    assert str(env.mint) not in done.stdout


def test_status_prints_creations(env):
    with Ledger.locked(env.ledger) as ledger:
        ledger.record("d", "w", 5, now=time.time(), creates_ata=True)
    body = json.loads(cli("status", "--config", str(env.config)).stdout)
    assert body["ata_creations"] == {"used": 1, "limit": LIMITS["max_ata_creations_24h"]}


def test_status_without_ledger_is_zero_and_creates_nothing(env):
    body = json.loads(cli("status", "--config", str(env.config)).stdout)
    assert body["transfers"]["used"] == 0
    assert body["paused"] is False
    assert not env.ledger.exists()


def test_bad_config_exits_config_error(env):
    done = cli("status", "--config", str(env.root / "missing"))
    assert json.loads(done.stdout) == {"verdict": "config_error"}
    assert done.returncode == 14


@pytest.mark.parametrize("key", list(LIMITS))
def test_config_missing_key_refused(env, key):
    env.write_config(**{key: None})
    with pytest.raises(ConfigError):
        load_config(env.config)


def test_unknown_key_and_bad_values_refused(env):
    for override in (
        {"extra_key": 1},
        {"max_transfers_24h": 0},
        {"max_ata_creations_24h": LIMITS["max_transfers_24h"] + 1},
        {"keypair_credential": "../x"},
        {"pause_file": "relative-pause"},
        {"ledger_path": "relative.sqlite"},
    ):
        env.write_config(**override)
        with pytest.raises(ConfigError):
            load_config(env.config)
