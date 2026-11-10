"""Durable ledger of issued signatures, serialised across processes by an exclusive file lock."""

import fcntl
import os
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

WINDOW_SECONDS = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS signatures (
    message_digest TEXT PRIMARY KEY,
    recipient TEXT NOT NULL,
    amount TEXT NOT NULL,
    signed_at INTEGER NOT NULL
) WITHOUT ROWID
"""


@dataclass(frozen=True)
class Usage:
    transfers: int
    total: int
    per_recipient: dict[str, int]


def _create_private(path: Path) -> None:
    os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))


class Ledger:
    """Open under the lock so a limit check and its insert are one atomic step.

    A class because the lock and the connection must live exactly as long as the
    critical section; callers cannot check limits without holding the lock.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    @contextmanager
    def locked(cls, path: Path):
        lock_path = path.with_name(path.name + ".lock")
        _create_private(lock_path)
        _create_private(path)
        with open(lock_path) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(path, isolation_level=None)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.execute(SCHEMA)
                yield cls(db)
            finally:
                db.close()

    @classmethod
    def read_usage(cls, path: Path, now: float) -> Usage:
        if not path.exists():
            return Usage(0, 0, {})
        with cls.locked(path) as ledger:
            return ledger.usage(now)

    def has(self, digest: str) -> bool:
        row = self._db.execute("SELECT 1 FROM signatures WHERE message_digest=?", (digest,))
        return row.fetchone() is not None

    def usage(self, now: float) -> Usage:
        rows = self._db.execute(
            "SELECT recipient, amount FROM signatures WHERE signed_at > ?",
            (int(now) - WINDOW_SECONDS,),
        ).fetchall()
        per_recipient: dict[str, int] = defaultdict(int)
        for recipient, amount in rows:
            per_recipient[recipient] += int(amount)
        return Usage(len(rows), sum(per_recipient.values()), dict(per_recipient))

    def record(self, digest: str, recipient: str, amount: int, now: float) -> None:
        """Commit durably (synchronous=FULL fsyncs) before the caller writes any output."""
        self._db.execute(
            "INSERT INTO signatures VALUES (?, ?, ?, ?)", (digest, recipient, str(amount), int(now))
        )
