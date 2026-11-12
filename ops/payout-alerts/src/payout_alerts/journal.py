"""journalctl subprocess reader: restarts with backoff, resumes after the last handled cursor."""

import json
import logging
import subprocess  # noqa: S404 (runs journalctl with a fixed argv list, no shell)
import threading
from collections.abc import Callable
from pathlib import Path

from .parse import alert_text
from .relay import Item, read_cursor, write_cursor

log = logging.getLogger("payout_alerts")
UNITS = ("payout-orchestrator.service", "payout-signer@*.service")
MAX_BACKOFF = 60.0


def command(cursor: str | None) -> list[str]:
    """journalctl argv; the cursor is our own, advanced only after delivery."""
    start = f"--after-cursor={cursor}" if cursor else "--since=now"
    units = [arg for unit in UNITS for arg in ("--unit", unit)]
    return ["journalctl", "--follow", "--output=json", "--no-pager", start, *units]


def parse_line(line: str) -> tuple[str | None, str | None]:
    """(cursor, alert text) of a journalctl JSON line; text is None for a non-alert entry."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None, None
    cursor = entry.get("__CURSOR") if isinstance(entry, dict) else None
    if not isinstance(cursor, str):
        return None, None
    return cursor, alert_text(entry)


def _handle(
    line: str, last: str | None, cursor_path: Path, submit: Callable[[Item], None]
) -> str | None:
    """Queue an alert line; the first non-alert cursor is saved while no cursor exists yet.
    Returns the newest cursor seen."""
    cursor, text = parse_line(line)
    if cursor and text:
        submit((cursor, text))
    elif cursor and read_cursor(cursor_path) is None:
        write_cursor(cursor_path, cursor)
    return cursor or last


def follow(
    cursor_path: Path,
    submit: Callable[[Item], None],
    stop: threading.Event,
    popen: Callable | None = None,
) -> None:
    """Read journalctl until `stop`; restart it with backoff whenever it exits."""
    spawn: Callable = popen or subprocess.Popen
    backoff, last = 1.0, None
    while not stop.is_set():
        proc = spawn(command(last or read_cursor(cursor_path)), stdout=subprocess.PIPE, text=True)
        for line in proc.stdout or ():
            backoff = 1.0
            last = _handle(line, last, cursor_path, submit)
            if stop.is_set():
                proc.terminate()
                return
        log.warning("journalctl exited with %s; restarting in %.0fs", proc.wait(), backoff)
        stop.wait(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)
