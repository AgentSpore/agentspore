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
MAX_BACKOFF = 60.0
MARK_EVERY = 1000  # non-alert entries between cursor markers
# INVARIANT(#182): no `--unit` filter. journalctl expands a `--unit` glob once, at start,
# against unit names already in the journal, so every new `payout-signer@<intent>.<epoch>`
# instance would be invisible; parse.WATCHED_UNIT does the filtering instead. Unfiltered,
# the seed cursor also always names an entry the follow command will see.
JOURNAL_TAIL = ["journalctl", "-n", "1", "--output=json", "--no-pager"]


def command(cursor: str | None) -> list[str]:
    """journalctl argv; the cursor is our own, advanced only after delivery."""
    start = f"--after-cursor={cursor}" if cursor else "--since=now"
    # INVARIANT(#182): journalctl block-buffers stdout into a pipe, so without stdbuf a live
    # alert waits in its buffer until about 4 KiB of later entries arrive (seen on the host).
    return [
        "stdbuf",
        "-oL",
        "journalctl",
        "--follow",
        "--output=json",
        "--no-pager",
        start,
    ]


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


def seed_cursor(cursor_path: Path, run: Callable) -> None:
    """With no saved cursor, save the newest journal entry's, so the first start and every
    later restart resume right after it instead of at `--since=now`. Nothing is saved when
    the journal cannot be read or is empty."""
    if read_cursor(cursor_path) is not None:
        return
    try:
        out = run(JOURNAL_TAIL, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return
    cursor = parse_line(out.strip())[0]
    if cursor:
        write_cursor(cursor_path, cursor)


def next_item(cursor: str | None, text: str | None, quiet: int) -> tuple[Item | None, int]:
    """(item to submit, new count of entries since the last submit): an alert, or a marker
    once MARK_EVERY non-alert entries have passed."""
    if not cursor:
        return None, quiet
    if text or quiet + 1 >= MARK_EVERY:
        return (cursor, text), 0
    return None, quiet + 1


def follow(
    cursor_path: Path,
    submit: Callable[[Item], None],
    stop: threading.Event,
    popen: Callable | None = None,
    run: Callable | None = None,
) -> None:
    """Read journalctl until `stop`; restart it with backoff whenever it exits."""
    spawn: Callable = popen or subprocess.Popen
    seed_cursor(cursor_path, run or subprocess.run)
    backoff, last, quiet = 1.0, None, 0
    while not stop.is_set():
        proc = spawn(command(last or read_cursor(cursor_path)), stdout=subprocess.PIPE, text=True)
        for line in proc.stdout or ():
            backoff = 1.0
            cursor, text = parse_line(line)
            last = cursor or last
            item, quiet = next_item(cursor, text, quiet)
            if item:
                submit(item)
            if stop.is_set():
                proc.terminate()
                return
        log.warning("journalctl exited with %s; restarting in %.0fs", proc.wait(), backoff)
        stop.wait(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)
