import json

from payout_orchestrator.journal import Journal


def test_journal_round_trip_holds_only_audit_fields(tmp_path):
    journal = Journal(tmp_path / "journal.json")
    assert journal.get("a") is None
    journal.put("a", 3, 250, 1.0)
    assert journal.get("a") == {"epoch": 3, "lvbh": 250, "at": 1.0}
    assert set(json.loads((tmp_path / "journal.json").read_text())["a"]) == {"epoch", "lvbh", "at"}
    journal.drop("a")
    assert journal.get("a") is None
    assert not (tmp_path / "journal.tmp").exists()
