"""Synthetic fixtures: no real addresses, no network."""

from pathlib import Path

import pytest
from solders.keypair import Keypair

LIMITS = {
    "max_amount_per_transfer": 1000,
    "max_amount_per_recipient_24h": 1500,
    "max_amount_total_24h": 2500,
    "max_transfers_24h": 3,
}


class Env:
    """Paths and a config writer for one test."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.mint = Keypair().pubkey()
        self.pause = root / "paused"
        self.config = root / "config.toml"
        self.ledger = root / "ledger.sqlite"
        self.write_config()

    def write_config(self, **overrides: object) -> None:
        values: dict[str, object] = {
            "keypair_credential": "keypair",
            "mint": str(self.mint),
            "decimals": 6,
            "pause_file": str(self.pause),
            "ledger_path": str(self.ledger),
            **LIMITS,
        } | overrides
        lines = [
            f'{k} = "{v}"' if isinstance(v, str) else f"{k} = {v}"
            for k, v in values.items()
            if v is not None
        ]
        self.config.write_text("\n".join(lines) + "\n")


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)
