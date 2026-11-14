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
