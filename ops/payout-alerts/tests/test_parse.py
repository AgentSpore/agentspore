import json

import pytest

from payout_alerts.parse import UNIT_FAILED_ID, alert_text


def entry(message, unit="payout-orchestrator.service", **extra):
    return {"_SYSTEMD_UNIT": unit, "MESSAGE": message, **extra}


def test_alert_line_rendered():
    msg = json.dumps({"alert": "lvbh_unproven", "intent_id": "ab-12", "verdict": "found"})
    assert alert_text(entry(msg)) == "alert=lvbh_unproven intent_id=ab-12 verdict=found"


def test_null_intent_and_error_key():
    msg = json.dumps({"alert": "x_failed", "intent_id": None, "error": "TimeoutExpired"})
    assert alert_text(entry(msg)) == "alert=x_failed error=TimeoutExpired"


def test_signer_instance_unit_accepted():
    unit = "payout-signer@ab-12.1700000000.service"
    assert alert_text(entry('{"alert": "a"}', unit=unit)) == "alert=a"


@pytest.mark.parametrize(
    "msg", ["plain line", "{not json", "[1, 2]", '{"alert": 5}', "{}", 7, None]
)
def test_non_alert_messages_ignored(msg):
    assert alert_text(entry(msg)) is None


def test_foreign_unit_ignored():
    assert alert_text(entry('{"alert": "a"}', unit="sshd.service")) is None
    assert alert_text({"MESSAGE": '{"alert": "a"}'}) is None


def test_unsafe_values_replaced_and_unknown_keys_dropped():
    msg = json.dumps({"alert": "a", "intent_id": "x y\nz", "error": "e" * 200, "extra": "leak"})
    out = alert_text(entry(msg))
    assert out == "alert=a intent_id=? error=?"
    assert "leak" not in out


def failed(result="exit-code", unit="payout-signer@a.1.service"):
    return {
        "_SYSTEMD_UNIT": "init.scope",
        "_PID": "1",
        "_UID": "0",
        "MESSAGE_ID": UNIT_FAILED_ID,
        "UNIT": unit,
        "UNIT_RESULT": result,
        "MESSAGE": "free text that must not be forwarded",
    }


def test_unit_failed_message():
    assert alert_text(failed()) == "unit_failed payout-signer@a.1.service exit-code"


def test_unit_failed_success_foreign_and_unsafe_result():
    assert alert_text(failed(result="success")) is None
    assert alert_text(failed(unit="nginx.service")) is None
    assert alert_text(failed(result="bad result!")) == "unit_failed payout-signer@a.1.service ?"


def test_unit_failed_from_another_process_is_ignored():
    for pid, uid in (("4242", "1000"), ("4242", "0"), ("1", "1000"), (None, None)):
        forged = {**failed(), "_PID": pid, "_UID": uid}
        assert alert_text(forged) is None


def test_welcome_pending_passes_only_numeric_counters():
    msg = json.dumps({"alert": "welcome_pending", "pending": 3, "clean": 0})
    assert alert_text(entry(msg, unit="welcome-pending-signal.service")) == (
        "alert=welcome_pending pending=3 clean=0"
    )


@pytest.mark.parametrize("bad", ["3", "3; rm", True, -1, 10**7, 1.5, None, [3]])
def test_welcome_pending_non_numbers_become_question_mark(bad):
    msg = json.dumps({"alert": "welcome_pending", "pending": bad, "clean": 1})
    out = alert_text(entry(msg, unit="welcome-pending-signal.service"))
    assert out == "alert=welcome_pending pending=? clean=1"


def test_counters_are_dropped_for_every_other_alert_and_extra_keys_never_pass():
    msg = json.dumps({"alert": "other", "pending": 3, "email": "a@b.c"})
    assert alert_text(entry(msg)) == "alert=other"
    msg = json.dumps({"alert": "welcome_pending", "pending": 1, "clean": 1, "email": "a@b.c"})
    assert alert_text(entry(msg)) == "alert=welcome_pending pending=1 clean=1"


BACKUP_UNIT = "agentspore-pg-backup.service"


@pytest.mark.parametrize(
    "stage", ["mkdir", "lock", "diskspace", "dump", "verify", "rename", "weekly"]
)
def test_backup_fail_forwards_allowlisted_stage(stage):
    msg = json.dumps({"backup": "fail", "stage": stage})
    assert alert_text(entry(msg, unit=BACKUP_UNIT)) == f"alert=backup_fail stage={stage}"


@pytest.mark.parametrize("stage", ["/var/x", "dump;rm", "DUMP", "", 5, None, ["dump"]])
def test_backup_fail_unknown_stage_becomes_question_mark(stage):
    msg = json.dumps({"backup": "fail", "stage": stage})
    assert alert_text(entry(msg, unit=BACKUP_UNIT)) == "alert=backup_fail stage=?"


def test_backup_fail_without_stage():
    msg = json.dumps({"backup": "fail"})
    assert alert_text(entry(msg, unit=BACKUP_UNIT)) == "alert=backup_fail stage=?"


def test_backup_ok_and_other_values_are_not_forwarded():
    ok = json.dumps({"backup": "ok", "size_bytes": 123, "toc": 4})
    assert alert_text(entry(ok, unit=BACKUP_UNIT)) is None
    assert alert_text(entry(json.dumps({"backup": "skipped"}), unit=BACKUP_UNIT)) is None


def test_backup_fail_extra_keys_never_pass():
    msg = json.dumps({"backup": "fail", "stage": "dump", "path": "/secret", "size_bytes": 9})
    assert alert_text(entry(msg, unit=BACKUP_UNIT)) == "alert=backup_fail stage=dump"


def test_backup_line_from_another_unit_ignored():
    msg = json.dumps({"backup": "fail", "stage": "dump"})
    assert alert_text(entry(msg, unit="sshd.service")) is None


def test_backup_branch_not_taken_for_other_watched_unit():
    msg = json.dumps({"backup": "fail", "stage": "dump", "alert": "x"})
    assert alert_text(entry(msg, unit="payout-orchestrator.service")) == "alert=x"
    msg = json.dumps({"backup": "fail", "stage": "dump"})
    assert alert_text(entry(msg, unit="payout-orchestrator.service")) is None


@pytest.mark.parametrize("unit", [BACKUP_UNIT, "payout-orchestrator.service"])
def test_deeply_nested_json_does_not_crash(unit):
    assert alert_text(entry("[" * 100000, unit=unit)) is None


def test_backup_unit_failed_is_forwarded_next_to_the_stage_alert():
    # Texts differ, so the 600 s dedupe does not merge them: the stage line says where the
    # script stopped, unit_failed also covers a run that died before writing any line.
    assert alert_text(failed(unit=BACKUP_UNIT)) == f"unit_failed {BACKUP_UNIT} exit-code"
