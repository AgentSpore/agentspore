"""Alert forwarder configuration (TOML): `chat_id` required, `prefix` optional."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CHAT_ID = re.compile(r"-?[0-9]{1,20}|@[A-Za-z0-9_]{1,64}")


class ConfigError(Exception):
    """Unreadable file, unknown key or an invalid value."""


@dataclass(frozen=True)
class Config:
    """Validated settings: target chat and an optional message prefix."""

    chat_id: str
    prefix: str = ""


def load_config(path: Path) -> Config:
    """Read and validate the TOML config; raises ConfigError."""
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config: {type(exc).__name__}") from None
    unknown = set(raw) - {"chat_id", "prefix"}
    chat_id, prefix = str(raw.get("chat_id", "")), raw.get("prefix", "")
    if unknown or not CHAT_ID.fullmatch(chat_id) or not isinstance(prefix, str) or len(prefix) > 64:
        raise ConfigError("invalid config: need chat_id, optional prefix, no other keys")
    return Config(chat_id, prefix)
