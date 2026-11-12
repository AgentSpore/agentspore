"""Journal entry to alert text. Only fixed keys with safe values ever leave the host."""

import json
import re

UNIT_FAILED_ID = "d9b373ed55a64feb8242e02dbe79a49c"
WATCHED_UNIT = re.compile(r"payout-orchestrator\.service|payout-signer@[A-Za-z0-9_.:-]+\.service")
SAFE_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
ALERT_KEYS = ("alert", "intent_id", "error", "verdict")


def safe(value: object) -> str:
    """The value itself when it is a short string of safe characters, else `?`."""
    if isinstance(value, str) and SAFE_VALUE.fullmatch(value):
        return value
    return "?"


def alert_text(entry: dict) -> str | None:
    """Text to forward for one journal entry, or None when it is not a payout alert."""
    if entry.get("MESSAGE_ID") == UNIT_FAILED_ID:
        return _unit_failed(entry)
    return _json_alert(entry)


def _unit_failed(entry: dict) -> str | None:
    # INVARIANT(#182): only PID 1 may report a failed unit. MESSAGE_ID, UNIT and UNIT_RESULT are
    # set by the sender, and any local user can write to the journal; `_PID`/`_UID` are set by
    # journald from the sender's credentials. (`journalctl --unit` used to do this check.)
    if entry.get("_PID") != "1" or entry.get("_UID") != "0":
        return None
    unit, result = entry.get("UNIT"), entry.get("UNIT_RESULT")
    if isinstance(unit, str) and WATCHED_UNIT.fullmatch(unit) and result and result != "success":
        return f"unit_failed {unit} {safe(result)}"
    return None


def _json_alert(entry: dict) -> str | None:
    source, message = entry.get("_SYSTEMD_UNIT"), entry.get("MESSAGE")
    if not (
        isinstance(source, str) and WATCHED_UNIT.fullmatch(source) and isinstance(message, str)
    ):
        return None
    try:
        body = json.loads(message)
    except ValueError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("alert"), str):
        return None
    return " ".join(f"{key}={safe(body[key])}" for key in ALERT_KEYS if body.get(key) is not None)
