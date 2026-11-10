import json
import os
import stat

import pytest

from payout_orchestrator.journal import Journal

from .fakes import Crash


def test_journal_round_trip_holds_only_audit_fields(tmp_path):
    journal = Journal(tmp_path / "journal.json")
    assert journal.get("a") is None
    journal.put("a", 3, 250, 1.0)
    assert journal.get("a") == {"epoch": 3, "lvbh": 250, "at": 1.0}
    assert set(json.loads((tmp_path / "journal.json").read_text())["a"]) == {"epoch", "lvbh", "at"}
    journal.drop("a")
    assert journal.get("a") is None
    assert not (tmp_path / "journal.tmp").exists()


def test_journal_file_mode_is_0600_and_directory_is_synced(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(
        os, "fsync", lambda fd: (synced.append(stat.S_ISDIR(os.fstat(fd).st_mode)), real(fd))
    )
    previous = os.umask(0)
    try:
        Journal(tmp_path / "journal.json").put("a", 1, 2, 3.0)
    finally:
        os.umask(previous)
    assert stat.S_IMODE((tmp_path / "journal.json").stat().st_mode) == 0o600
    assert synced == [False, True]


def test_crash_before_rename_keeps_previous_state(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.json")
    journal.put("a", 1, 2, 3.0)

    def die(*args):
        raise Crash

    monkeypatch.setattr(os, "replace", die)
    with pytest.raises(Crash):
        journal.put("a", 2, 9, 4.0)
    assert journal.get("a") == {"epoch": 1, "lvbh": 2, "at": 3.0}


def test_hold_keeps_existing_gate_and_marks_reason(tmp_path):
    journal = Journal(tmp_path / "journal.json")
    journal.put("a", 3, 250, 1.0)
    journal.hold("a", "rejected", 2.0)
    assert journal.get("a") == {"epoch": 3, "lvbh": 250, "at": 1.0, "hold": "rejected"}
    journal.hold("b", "x", 2.0)
    assert (journal.get("b") or {})["hold"] == "x"
