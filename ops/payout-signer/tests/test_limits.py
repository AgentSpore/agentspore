import pytest

from payout_signer.config import load_config
from payout_signer.ledger import Usage
from payout_signer.limits import Transfer, within_limits

from .conftest import LIMITS

R = "recipient-a"


@pytest.fixture
def cfg(env):
    return load_config(env.config)


def test_per_transfer_boundary(cfg):
    top = LIMITS["max_amount_per_transfer"]
    assert within_limits(cfg, Usage(0, 0, {}), Transfer(R, top))
    assert not within_limits(cfg, Usage(0, 0, {}), Transfer(R, top + 1))


def test_per_recipient_boundary(cfg):
    usage = Usage(1, 1000, {R: 1000})
    assert within_limits(cfg, usage, Transfer(R, 500))
    assert not within_limits(cfg, usage, Transfer(R, 501))
    assert within_limits(cfg, usage, Transfer("recipient-b", 501))


def test_total_boundary(cfg):
    usage = Usage(2, 2000, {"x": 1000, "y": 1000})
    assert within_limits(cfg, usage, Transfer(R, 500))
    assert not within_limits(cfg, usage, Transfer(R, 501))


def test_count_boundary(cfg):
    last = LIMITS["max_transfers_24h"] - 1
    assert within_limits(cfg, Usage(last, 0, {}), Transfer(R, 1))
    assert not within_limits(cfg, Usage(last + 1, 0, {}), Transfer(R, 1))


def test_creations_boundary(cfg):
    last = LIMITS["max_ata_creations_24h"] - 1
    creating = Transfer(R, 1, creates_ata=True)
    assert within_limits(cfg, Usage(1, 1, {}, last), creating)
    assert not within_limits(cfg, Usage(1, 1, {}, last + 1), creating)
    assert within_limits(cfg, Usage(1, 1, {}, last + 1), Transfer(R, 1))
