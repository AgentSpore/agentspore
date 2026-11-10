"""Status report: numbers only, never addresses or key material."""

import time
from pathlib import Path

from payout_signer.config import load_config
from payout_signer.ledger import WINDOW_SECONDS, Ledger


def status(config_path: Path, now: float | None = None) -> dict:
    """Usage in the rolling window against each limit, plus the pause flag."""
    now = time.time() if now is None else now
    config = load_config(config_path)
    usage = Ledger.read_usage(config.ledger_path, now)
    return {
        "paused": config.paused,
        "window_seconds": WINDOW_SECONDS,
        "transfers": {"used": usage.transfers, "limit": config.max_transfers_24h},
        "total": {"used": usage.total, "limit": config.max_amount_total_24h},
        "recipient_max": {
            "used": max(usage.per_recipient.values(), default=0),
            "limit": config.max_amount_per_recipient_24h,
        },
        "per_transfer_limit": config.max_amount_per_transfer,
    }
