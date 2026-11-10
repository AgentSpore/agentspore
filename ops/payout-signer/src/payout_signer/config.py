"""Strict configuration and credential loading. No defaults: a missing key refuses to start."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from solders.pubkey import Pubkey

MAX_U63 = 2**63 - 1
LIMIT_KEYS = (
    "max_amount_per_transfer",
    "max_amount_per_recipient_24h",
    "max_amount_total_24h",
    "max_transfers_24h",
)


class ConfigError(Exception):
    """Configuration or credential is unusable; the message never carries values."""


@dataclass(frozen=True)
class Config:
    keypair_credential: str
    mint: Pubkey
    decimals: int
    pause_file: Path
    ledger_path: Path
    max_amount_per_transfer: int
    max_amount_per_recipient_24h: int
    max_amount_total_24h: int
    max_transfers_24h: int

    @property
    def paused(self) -> bool:
        return os.path.lexists(self.pause_file)


def _int(raw: dict, key: str, low: int, high: int) -> int:
    value = raw.get(key)
    if type(value) is not int or not low <= value <= high:
        raise ConfigError(key)
    return value


def _str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if type(value) is not str or not value:
        raise ConfigError(key)
    return value


def _absolute(raw: dict, key: str) -> Path:
    """State paths must not depend on the service's working directory."""
    path = Path(_str(raw, key))
    if not path.is_absolute():
        raise ConfigError(key)
    return path


def load_config(path: Path) -> Config:
    """Parse the TOML file; every key is required and unknown keys are refused."""
    try:
        raw = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise ConfigError("unreadable") from None
    names = {*Config.__dataclass_fields__}
    if set(raw) != names:
        raise ConfigError("keys")
    credential = _str(raw, "keypair_credential")
    if "/" in credential or credential.startswith("."):
        raise ConfigError("keypair_credential")
    try:
        mint = Pubkey.from_string(_str(raw, "mint"))
    except ValueError:
        raise ConfigError("mint") from None
    limits = {key: _int(raw, key, 1, MAX_U63) for key in LIMIT_KEYS}
    return Config(
        keypair_credential=credential,
        mint=mint,
        decimals=_int(raw, "decimals", 0, 255),
        pause_file=_absolute(raw, "pause_file"),
        ledger_path=_absolute(raw, "ledger_path"),
        **limits,
    )
