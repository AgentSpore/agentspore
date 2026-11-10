"""Tick configuration from a TOML or JSON file; every identifier is a placeholder in the example."""

import json
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Config file missing keys, carrying unknown keys or holding out-of-range values."""


@dataclass(frozen=True)
class Config:
    """Validated tick settings; `per_tick` bounds intents acted on, not rows fetched."""

    container: str
    worker_id: str
    handoff_dir: Path
    journal_path: Path
    pause_path: Path
    per_tick: int
    container_request_dir: str = "/run/payout"
    lease_ttl_seconds: int = 300
    step_timeout_seconds: int = 60
    max_resends: int = 2

    @property
    def lock_path(self) -> Path:
        return self.journal_path.with_suffix(".lock")


REQUIRED = ("container", "worker_id", "handoff_dir", "journal_path", "pause_path", "per_tick")
PATHS = ("handoff_dir", "journal_path", "pause_path")


def load_config(path: Path) -> Config:
    """Parse the file; unknown or missing keys and bad ranges fail closed."""
    raw = Path(path).read_bytes()
    body = json.loads(raw) if str(path).endswith(".json") else tomllib.loads(raw.decode())
    names = {field.name for field in fields(Config)}
    if not set(REQUIRED) <= body.keys() or not body.keys() <= names:
        raise ConfigError("config keys")
    values: dict[str, Any] = {k: Path(v) if k in PATHS else v for k, v in body.items()}
    config = Config(**values)
    if not (1 <= config.per_tick <= 1000 and 1 <= config.lease_ttl_seconds <= 300):
        raise ConfigError("config range")
    if config.max_resends < 0 or config.step_timeout_seconds < 1:
        raise ConfigError("config range")
    return config
