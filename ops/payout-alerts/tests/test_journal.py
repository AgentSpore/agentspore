import json
import threading

from payout_alerts.journal import command, follow, parse_line

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
    assert parse_line(json.dumps({"__CURSOR": "c1", "MESSAGE": "x"})) == ("c1", None)
    assert parse_line("garbage") == (None, None)
    assert parse_line(json.dumps(ALERT)) == (None, None)


class Proc:
    def __init__(self, lines, on_read=None):
        self.stdout, self.terminated, self.on_read = self._iter(lines), False, on_read

    def _iter(self, lines):
        for item in lines:
            if self.on_read:
                self.on_read()
            yield item

    def wait(self):
        return 1

    def terminate(self):
        self.terminated = True


def line(cursor, message="x"):
    entry = {"__CURSOR": cursor, "MESSAGE": message, "_SYSTEMD_UNIT": ALERT["_SYSTEMD_UNIT"]}
    return json.dumps(entry) + "\n"


def run_follow(tmp_path, procs, stop=None):
    stop, calls, items = stop or threading.Event(), [], []

    def popen(cmd, **kwargs):
        calls.append(cmd)
        if not procs:
            stop.set()
            return Proc([])
        return procs.pop(0)

    follow(tmp_path / "cursor", items.append, stop, popen=popen)
    return calls, items


def test_follow_restarts_after_last_cursor_even_for_non_alerts(tmp_path):
    first = Proc([line("c1"), line("c2", '{"alert": "a"}')])
    calls, items = run_follow(tmp_path, [first])
    assert items == [("c2", "alert=a")]
    assert "--since=now" in calls[0] and "--after-cursor=c2" in calls[1]


def test_follow_persists_first_non_alert_cursor(tmp_path):
    run_follow(tmp_path, [Proc([line("c1"), line("c2")])])
    assert (tmp_path / "cursor").read_text() == "c1"


def test_follow_resumes_from_cursor_file_and_keeps_it(tmp_path):
    (tmp_path / "cursor").write_text("saved")
    calls, _ = run_follow(tmp_path, [Proc([line("c5")])])
    assert "--after-cursor=saved" in calls[0]
    assert (tmp_path / "cursor").read_text() == "saved"


def test_follow_stop_terminates_process(tmp_path):
    stop = threading.Event()
    proc = Proc([line("c1"), line("c2")], on_read=stop.set)
    calls, _ = run_follow(tmp_path, [proc], stop)
    assert proc.terminated and len(calls) == 1
