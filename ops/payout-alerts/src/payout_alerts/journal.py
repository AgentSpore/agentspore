"""journalctl subprocess reader: restarts with backoff, resumes after the last handled cursor."""

import json
import logging
import subprocess  # noqa: S404 (runs journalctl with a fixed argv list, no shell)
import threading
from collections.abc import Callable
from pathlib import Path

from .parse import alert_text
from .relay import Item, read_cursor

log = logging.getLogger("payout_alerts")
UNITS = ("payout-orchestrator.service", "payout-signer@*.service")
MAX_BACKOFF = 60.0


def command(cursor: str | None) -> list[str]:
    """journalctl argv; the cursor is our own, advanced only after delivery."""
    start = f"--after-cursor={cursor}" if cursor else "--since=now"
    units = [arg for unit in UNITS for arg in ("--unit", unit)]
    return ["journalctl", "--follow", "--output=json", "--no-pager", start, *units]


def parse_line(line: str) -> Item | None:
    """(cursor, text) for an alert line of journalctl JSON output, else None."""
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    cursor = entry.get("__CURSOR") if isinstance(entry, dict) else None
    text = alert_text(entry) if isinstance(cursor, str) else None
    return (cursor, text) if isinstance(cursor, str) and text else None


def follow(
    cursor_path: Path,
    submit: Callable[[Item], None],
    stop: threading.Event,
    popen: Callable = subprocess.Popen,
) -> None:
    """Read journalctl until `stop`; restart it with backoff whenever it exits."""
    backoff = 1.0
    while not stop.is_set():
        proc = popen(command(read_cursor(cursor_path)), stdout=subprocess.PIPE, text=True)
        for line in proc.stdout:
            backoff = 1.0
            item = parse_line(line)
            if item:
                submit(item)
            if stop.is_set():
                proc.terminate()
                return
        log.warning("journalctl exited with %s; restarting in %.0fs", proc.wait(), backoff)
        stop.wait(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)
