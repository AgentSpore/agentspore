"""Real SQLite quota and crash-safety acceptance tests; no platform requests."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from agentspore_sdk.operator_ledger import OperatorLedger

NOW = datetime(2026, 10, 21, 21, 30, tzinfo=timezone.utc)
DIGEST = "a" * 64


@pytest.fixture
def ledger(tmp_path):
    instance = OperatorLedger(tmp_path / "private" / "ledger.db")
    yield instance
    instance.close()


def test_quota_counts_failed_and_interrupted_runs_and_changes_utc_day(ledger):
    for state in ("success", "failed", "interrupted", "failed"):
        run = ledger.reserve_analysis(NOW, DIGEST)
        assert run is not None
        ledger.finish_analysis(run, state, NOW)
    assert ledger.reserve_analysis(NOW, DIGEST) is None
    assert ledger.reserve_analysis(NOW + timedelta(days=1), DIGEST) is not None


def test_active_analysis_survives_reopen_without_expiring(tmp_path):
    path = tmp_path / "ledger.db"
    first = OperatorLedger(path)
    run = first.reserve_analysis(NOW, DIGEST)
    assert run is not None
    first.close()
    with OperatorLedger(path) as reopened:
        assert reopened.reserve_analysis(NOW + timedelta(days=20), DIGEST) is None
        reopened.finish_analysis(run, "interrupted", NOW + timedelta(days=20))
        assert reopened.reserve_analysis(NOW + timedelta(days=20), DIGEST) is not None


@pytest.mark.parametrize("operation", ["analysis", "publication"])
def test_separate_connections_cannot_reserve_same_slot(tmp_path, operation):
    path = tmp_path / "ledger.db"
    with OperatorLedger(path):
        pass
    barrier = Barrier(2)

    def compete(_):
        with OperatorLedger(path) as instance:
            barrier.wait(timeout=5)
            if operation == "analysis":
                return instance.reserve_analysis(NOW, DIGEST)
            return instance.reserve_publication(NOW, "chat", DIGEST)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sum(bool(item) for item in results) == 1


@pytest.mark.parametrize("receipt", ["42", None, "reserved"])
def test_publication_states_block_new_digest_after_restart(tmp_path, receipt):
    path = tmp_path / "ledger.db"
    with OperatorLedger(path) as instance:
        assert instance.reserve_publication(NOW, "chat", DIGEST)
        if receipt != "reserved":
            instance.finish_publication(NOW, "chat", DIGEST, receipt)
    with OperatorLedger(path) as reopened:
        assert not reopened.reserve_publication(NOW, "chat", "b" * 64)
        assert reopened.reserve_publication(NOW, "blog", DIGEST)


def test_publication_uses_moscow_day_and_receipt_is_recorded_once(ledger):
    assert ledger.reserve_publication(NOW, "chat", DIGEST)
    ledger.finish_publication(NOW, "chat", DIGEST, "post-42")
    assert ledger._db.execute(
        "SELECT state, receipt_id FROM publications"
    ).fetchone() == ("sent", "post-42")
    with pytest.raises(ValueError):
        ledger.finish_publication(NOW, "chat", DIGEST, "post-43")
    assert not ledger.reserve_publication(NOW + timedelta(hours=2), "chat", DIGEST)
    assert ledger.reserve_publication(NOW + timedelta(days=1), "chat", DIGEST)


def test_process_crash_keeps_committed_reservations(tmp_path):
    path = tmp_path / "ledger.db"
    script = (
        "import os, sys; from datetime import datetime; "
        "from pathlib import Path; "
        "from agentspore_sdk.operator_ledger import OperatorLedger; "
        "ledger=OperatorLedger(Path(sys.argv[1])); "
        "now=datetime.fromisoformat(sys.argv[2]); "
        "ledger.reserve_analysis(now, 'a'*64); "
        "ledger.reserve_publication(now, 'chat', 'a'*64); "
        "os._exit(17)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), NOW.isoformat()], timeout=10
    )
    assert result.returncode == 17
    with OperatorLedger(path) as reopened:
        assert reopened.reserve_analysis(NOW, DIGEST) is None
        assert not reopened.reserve_publication(NOW, "chat", DIGEST)


def test_private_permissions_and_durability_pragmas(ledger, tmp_path):
    path = tmp_path / "private" / "ledger.db"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert ledger._db.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert ledger._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert 0 < ledger._db.execute("PRAGMA busy_timeout").fetchone()[0] <= 5000


@pytest.mark.parametrize("bad", ["", "g" * 64, "a" * 63, "A" * 64])
def test_invalid_digest_never_reserves(ledger, bad):
    with pytest.raises(ValueError):
        ledger.reserve_analysis(NOW, bad)
    with pytest.raises(ValueError):
        ledger.reserve_publication(NOW, "chat", bad)


def test_invalid_clock_channel_state_and_receipt_fail_closed(ledger):
    with pytest.raises(ValueError):
        ledger.reserve_analysis(NOW.replace(tzinfo=None), DIGEST)
    with pytest.raises(ValueError):
        ledger.reserve_publication(NOW, "dm", DIGEST)
    run = ledger.reserve_analysis(NOW, DIGEST)
    with pytest.raises(ValueError):
        ledger.finish_analysis(run, "unknown", NOW)
    with pytest.raises(ValueError):
        ledger.finish_analysis("unknown", "success", NOW)
    assert ledger.reserve_publication(NOW, "chat", DIGEST)
    for digest, receipt in (
        ("b" * 64, "42"),
        (DIGEST, "x" * 129),
        (DIGEST, "\nsecret"),
    ):
        with pytest.raises(ValueError):
            ledger.finish_publication(NOW, "chat", digest, receipt)
    ledger.finish_publication(NOW, "chat", DIGEST, "42")


def test_existing_world_readable_or_symlink_database_is_rejected(tmp_path):
    path = tmp_path / "ledger.db"
    path.touch(mode=0o644)
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        OperatorLedger(path)
    link = tmp_path / "link.db"
    link.symlink_to(path)
    with pytest.raises(OSError):
        OperatorLedger(link)


def test_transaction_failure_rolls_back_and_releases_writer(ledger):
    with pytest.raises(RuntimeError):
        with ledger._transaction():
            ledger._db.execute(
                "INSERT INTO analyses VALUES (?, ?, ?, ?, ?, NULL)",
                ("b" * 32, "2026-10-21", DIGEST, "active", NOW.isoformat()),
            )
            raise RuntimeError("synthetic interrupted transaction")
    assert ledger.reserve_analysis(NOW, DIGEST) is not None


def test_utc_midnight_resets_quota_without_resetting_moscow_publication(ledger):
    before = NOW.replace(hour=23, minute=59)
    for _ in range(4):
        run = ledger.reserve_analysis(before, DIGEST)
        assert run is not None
        ledger.finish_analysis(run, "failed", before)
    assert ledger.reserve_analysis(before, DIGEST) is None
    assert ledger.reserve_publication(before, "chat", DIGEST)
    after = before + timedelta(minutes=2)
    assert ledger.reserve_analysis(after, DIGEST) is not None
    assert not ledger.reserve_publication(after, "chat", DIGEST)
