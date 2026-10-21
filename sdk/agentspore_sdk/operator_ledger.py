"""Durable local quotas owned by the trusted operator, never by the analyst."""

import os
import re
import sqlite3
import stat
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


class OperatorLedger:
    """Serialize quota reservations and retain ambiguous deliveries across crashes."""

    def __init__(self, path: Path) -> None:
        """Open a private ledger; reject unsafe existing directory or file modes."""
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.stat()
        if (
            path.parent.is_symlink()
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o777 != 0o700
        ):
            raise PermissionError("Ledger directory must be private")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o777 != 0o600
            ):
                raise PermissionError("Ledger file must be private")
        finally:
            os.close(descriptor)
        self._db = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            with self._transaction():
                self._db.execute("""CREATE TABLE IF NOT EXISTS analyses (
                    run_id TEXT PRIMARY KEY, utc_day TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL, state TEXT NOT NULL
                    CHECK(state IN ('active','success','failed','interrupted')),
                    started_at TEXT NOT NULL, finished_at TEXT)""")
                self._db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS
                    one_active_analysis
                    ON analyses(state) WHERE state='active'""")
                self._db.execute("""CREATE TABLE IF NOT EXISTS publications (
                    local_day TEXT NOT NULL,
                    channel TEXT NOT NULL CHECK(channel IN ('chat','blog')),
                    snapshot_digest TEXT NOT NULL, state TEXT NOT NULL
                    CHECK(state IN ('reserved','sent','uncertain')),
                    reserved_at TEXT NOT NULL, receipt_id TEXT,
                    PRIMARY KEY(local_day, channel))""")
        except BaseException:
            self._db.close()
            raise

    def __enter__(self) -> "OperatorLedger":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close this connection without clearing reservations or active analyses."""
        self._db.close()

    @contextmanager
    def _transaction(self) -> Generator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    @staticmethod
    def _stamp(now: datetime) -> tuple[str, str, str]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Clock must be timezone-aware")
        utc = now.astimezone(timezone.utc)
        return (
            utc.isoformat(),
            utc.date().isoformat(),
            now.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat(),
        )

    @staticmethod
    def _digest(digest: str) -> None:
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Invalid snapshot digest")

    def reserve_analysis(self, now: datetime, digest: str) -> str | None:
        """Commit one of four UTC-day slots, or deny while any analysis is active."""
        stamp, day, _ = self._stamp(now)
        self._digest(digest)
        with self._transaction():
            count = self._db.execute(
                "SELECT COUNT(*) FROM analyses WHERE utc_day=?", (day,)
            ).fetchone()[0]
            active = self._db.execute(
                "SELECT 1 FROM analyses WHERE state='active'"
            ).fetchone()
            if count >= 4 or active is not None:
                return None
            run = uuid4().hex
            self._db.execute(
                "INSERT INTO analyses VALUES (?, ?, ?, 'active', ?, NULL)",
                (run, day, digest, stamp),
            )
        return run

    def finish_analysis(self, run_id: str, state: str, now: datetime) -> None:
        """Finalize a run; interrupted requires proof that its process terminated."""
        stamp, _, _ = self._stamp(now)
        if (
            state not in ("success", "failed", "interrupted")
            or re.fullmatch(r"[0-9a-f]{32}", run_id) is None
        ):
            raise ValueError("Invalid analysis completion")
        with self._transaction():
            changed = self._db.execute(
                """UPDATE analyses SET state=?, finished_at=?
                WHERE run_id=? AND state='active' AND started_at<=?""",
                (state, stamp, run_id, stamp),
            )
            if changed.rowcount != 1:
                raise ValueError("Analysis is not active or clock precedes reservation")

    def reserve_publication(self, now: datetime, channel: str, digest: str) -> bool:
        """Reserve chat or blog once per Moscow day before any external delivery."""
        stamp, _, day = self._stamp(now)
        self._digest(digest)
        if channel not in ("chat", "blog"):
            raise ValueError("Unknown publication channel")
        with self._transaction():
            changed = self._db.execute(
                """INSERT INTO publications VALUES (?, ?, ?, 'reserved', ?, NULL)
                ON CONFLICT(local_day, channel) DO NOTHING""",
                (day, channel, digest, stamp),
            )
        return changed.rowcount == 1

    def finish_publication(
        self, reserved_at: datetime, channel: str, digest: str, receipt_id: str | None
    ) -> None:
        """Finalize with the original captured reserve_publication timestamp."""
        stamp, _, day = self._stamp(reserved_at)
        self._digest(digest)
        if channel not in ("chat", "blog") or (
            receipt_id is not None
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", receipt_id) is None
        ):
            raise ValueError("Invalid publication completion")
        state = "uncertain" if receipt_id is None else "sent"
        with self._transaction():
            changed = self._db.execute(
                """UPDATE publications SET state=?, receipt_id=?
                WHERE local_day=? AND channel=? AND snapshot_digest=?
                AND state='reserved' AND reserved_at<=?""",
                (state, receipt_id, day, channel, digest, stamp),
            )
            if changed.rowcount != 1:
                raise ValueError("Publication does not match an unfinished reservation")
