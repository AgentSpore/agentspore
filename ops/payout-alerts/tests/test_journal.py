import json

from payout_alerts.journal import command, parse_line

ALERT = {"_SYSTEMD_UNIT": "payout-orchestrator.service", "MESSAGE": '{"alert": "a"}'}


def test_command_first_start_uses_since_now():
    cmd = command(None)
    assert "--since=now" in cmd and not any(a.startswith("--after-cursor") for a in cmd)
    assert cmd[0] == "journalctl" and "--follow" in cmd and "--output=json" in cmd


def test_command_resumes_after_cursor():
    cmd = command("s=abc;i=1")
    assert "--after-cursor=s=abc;i=1" in cmd and "--since=now" not in cmd


def test_parse_line_alert_and_non_alert():
    assert parse_line(json.dumps({"__CURSOR": "c9", **ALERT})) == ("c9", "alert=a")
    assert parse_line(json.dumps({"__CURSOR": "c1", "MESSAGE": "x"})) is None
    assert parse_line("garbage") is None
    assert parse_line(json.dumps(ALERT)) is None
