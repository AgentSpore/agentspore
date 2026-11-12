"""Queue, dedupe, bounded retry and cursor persistence between the journal and the sender."""

import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .telegram import SendError

log = logging.getLogger("payout_alerts")
MAX_WAIT = 60.0
PERMANENT = frozenset({400, 401, 403, 404})

Item = tuple[str, str | None]  # (journal cursor, alert text); text None = cursor marker


def read_cursor(path: Path) -> str | None:
    """Saved journal cursor, or None before the first one was written."""
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


def write_cursor(path: Path, cursor: str) -> None:
    """Atomically replace the saved cursor."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(cursor)
    os.replace(tmp, path)


@dataclass(frozen=True)
class RelayOptions:
    window: float = 600.0
    queue_size: int = 100
    attempts: int = 3


class Relay:
    """Sends queued alerts one by one; the cursor moves only once an item is sent or dropped."""

    def __init__(
        self,
        send: Callable[[str], None],
        cursor_path: Path,
        options: RelayOptions | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self._send, self._cursor_path = send, cursor_path
        options = options or RelayOptions()
        self._window, self._attempts = options.window, options.attempts
        self._clock, self._sleep = clock, sleep
        self._queue: queue.Queue[Item] = queue.Queue(maxsize=options.queue_size)
        self._seen: dict[str, tuple[float, int]] = {}
        self._dropped = 0

    def submit(self, item: Item) -> None:
        """Queue an item; when full the oldest alert is dropped and counted. A cursor marker
        never evicts anything: on a full queue it is discarded, uncounted."""
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                if item[1] is None:
                    return
                try:
                    if self._queue.get_nowait()[1] is not None:
                        self._dropped += 1
                except queue.Empty:
                    pass

    def run(self, stop: threading.Event) -> None:
        """Process queued items until `stop` is set."""
        while not stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            self.process(item)

    def process(self, item: Item) -> None:
        """Send one alert unless it is a recent duplicate, then advance the cursor; a marker
        (no text) only advances the cursor."""
        cursor, text = item
        if text is None:
            # INVARIANT(#182): while an evicted alert is counted only in memory (`_dropped`),
            # a marker must not move the cursor past it, or a restart loses that alert silently.
            if not self._dropped:
                write_cursor(self._cursor_path, cursor)
            return
        admitted = self._admit(text)
        sent = self._deliver(admitted[0]) if admitted is not None else False
        if sent is None:
            return
        if admitted is not None and sent:
            now = self._clock()
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self._window}
            self._seen[text] = (now, 0)
            self._dropped -= admitted[1]
        write_cursor(self._cursor_path, cursor)

    def _admit(self, text: str) -> tuple[str, int] | None:
        """(text with a note on earlier suppressed or dropped alerts, dropped count it reports);
        None inside the dedupe window. Nothing is recorded until the send succeeds."""
        now = self._clock()
        first, suppressed = self._seen.get(text, (None, 0))
        if first is not None and now - first < self._window:
            self._seen[text] = (first, suppressed + 1)
            return None
        dropped = self._dropped
        notes = [f"+{suppressed} suppressed"] if suppressed else []
        notes += [f"+{dropped} dropped"] if dropped else []
        return (f"{text} ({', '.join(notes)})" if notes else text), dropped

    def _deliver(self, body: str) -> bool | None:
        """True when sent, False when dropped (permanent 4xx or retries used up), None when
        a retry wait was interrupted by stop: the cursor must then stay where it is."""
        for attempt in range(1, self._attempts + 1):
            try:
                self._send(body)
                return True
            except SendError as exc:
                log.warning("send failed: %s status=%s attempt=%d", exc.kind, exc.status, attempt)
                if exc.status in PERMANENT:
                    break
                if attempt < self._attempts and self._sleep(
                    min(exc.retry_after or 2.0**attempt, MAX_WAIT)
                ):
                    return None
        log.error("alert dropped after failed send")
        return False
