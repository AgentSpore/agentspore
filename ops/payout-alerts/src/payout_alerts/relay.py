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

Item = tuple[str, str]  # (journal cursor, alert text)


def read_cursor(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except FileNotFoundError:
        return None


def write_cursor(path: Path, cursor: str) -> None:
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._send, self._cursor_path = send, cursor_path
        options = options or RelayOptions()
        self._window, self._attempts = options.window, options.attempts
        self._clock, self._sleep = clock, sleep
        self._queue: queue.Queue[Item] = queue.Queue(maxsize=options.queue_size)
        self._seen: dict[str, tuple[float, int]] = {}
        self._dropped = 0

    def submit(self, item: Item) -> None:
        """Queue an item; when full the oldest is dropped and counted."""
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._dropped += 1
                except queue.Empty:
                    pass

    def drain(self) -> int:
        count = 0
        while not self._queue.empty():
            self.process(self._queue.get_nowait())
            count += 1
        return count

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            self.process(item)

    def process(self, item: Item) -> None:
        cursor, text = item
        body = self._admit(text)
        if body is not None:
            self._deliver(body)
        write_cursor(self._cursor_path, cursor)

    def _admit(self, text: str) -> str | None:
        """Text with a suffix for earlier suppressed or dropped alerts; None inside the window."""
        now = self._clock()
        first, suppressed = self._seen.get(text, (None, 0))
        if first is not None and now - first < self._window:
            self._seen[text] = (first, suppressed + 1)
            return None
        self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self._window}
        self._seen[text] = (now, 0)
        notes = [f"+{suppressed} suppressed"] if suppressed else []
        if self._dropped:
            notes.append(f"+{self._dropped} dropped")
            self._dropped = 0
        return f"{text} ({', '.join(notes)})" if notes else text

    def _deliver(self, body: str) -> None:
        for attempt in range(1, self._attempts + 1):
            try:
                self._send(body)
                return
            except SendError as exc:
                log.warning("send failed: %s status=%s attempt=%d", exc.kind, exc.status, attempt)
                if attempt < self._attempts:
                    self._sleep(min(exc.retry_after or 2.0**attempt, MAX_WAIT))
        log.error("alert dropped after %d failed attempts", self._attempts)
