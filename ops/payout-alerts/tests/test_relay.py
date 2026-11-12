import logging

from payout_alerts.relay import Relay, RelayOptions
from payout_alerts.telegram import SendError


class Sender:
    def __init__(self, cursor_file, failures=()):
        self.sent, self.seen_cursor, self.failures = [], [], list(failures)
        self.cursor_file = cursor_file

    def send(self, text):
        self.seen_cursor.append(self.cursor_file.read_text() if self.cursor_file.exists() else None)
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(text)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def make(tmp_path, failures=(), **kw):
    cursor = tmp_path / "cursor"
    sender, clock, sleeps = Sender(cursor, failures), Clock(), []
    relay = Relay(sender.send, cursor, RelayOptions(**kw), clock=clock, sleep=sleeps.append)
    return relay, sender, clock, sleeps, cursor


def test_cursor_advanced_only_after_send(tmp_path):
    relay, sender, _, _, cursor = make(tmp_path)
    cursor.write_text("old")
    relay.process(("c1", "alert=a"))
    assert sender.seen_cursor == ["old"]
    assert cursor.read_text() == "c1"


def test_cursor_stays_while_retrying_and_moves_when_dropped(tmp_path):
    err = SendError(None, None, "OSError")
    relay, sender, _, _, cursor = make(tmp_path, failures=[err] * 3, attempts=3)
    relay.process(("c1", "alert=a"))
    assert sender.sent == [] and sender.seen_cursor == [None] * 3
    assert cursor.read_text() == "c1"


def test_retry_then_success(tmp_path):
    relay, sender, _, _, _ = make(tmp_path, failures=[SendError(500, None, "HTTPError")])
    relay.process(("c1", "alert=a"))
    assert sender.sent == ["alert=a"]


def test_429_sleeps_retry_after(tmp_path):
    relay, sender, _, sleeps, _ = make(tmp_path, failures=[SendError(429, 7, "HTTPError")])
    relay.process(("c1", "alert=a"))
    assert sleeps == [7] and sender.sent == ["alert=a"]


def test_dedupe_window_and_suppressed_suffix(tmp_path):
    relay, sender, clock, _, cursor = make(tmp_path, window=600)
    relay.process(("c1", "alert=a"))
    clock.now = 100
    relay.process(("c2", "alert=a"))
    relay.process(("c3", "alert=a"))
    assert sender.sent == ["alert=a"] and cursor.read_text() == "c3"
    clock.now = 700
    relay.process(("c4", "alert=a"))
    assert sender.sent[-1] == "alert=a (+2 suppressed)"
    relay.process(("c5", "alert=b"))
    assert sender.sent[-1] == "alert=b"


def test_queue_drops_oldest_and_counts(tmp_path):
    relay, sender, _, _, _ = make(tmp_path, queue_size=2)
    for i in range(4):
        relay.submit((f"c{i}", f"alert=a{i}"))
    while not relay._queue.empty():
        relay.process(relay._queue.get_nowait())
    assert sender.sent == ["alert=a2 (+2 dropped)", "alert=a3"]


def test_failure_logs_class_and_status_only(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    relay, _, _, _, _ = make(tmp_path, failures=[SendError(401, None, "HTTPError")] * 3)
    relay.process(("c1", "alert=a"))
    assert "HTTPError" in caplog.text and "401" in caplog.text


def test_dedupe_marked_only_after_successful_send(tmp_path):
    err = SendError(None, None, "OSError")
    relay, sender, clock, _, _ = make(tmp_path, failures=[err] * 3)
    relay.process(("c1", "alert=a"))
    clock.now = 60
    relay.process(("c2", "alert=a"))
    assert sender.sent == ["alert=a"]


def test_permanent_4xx_not_retried(tmp_path):
    relay, sender, _, sleeps, _ = make(tmp_path, failures=[SendError(401, None, "HTTPError")] * 3)
    relay.process(("c1", "alert=a"))
    assert len(sender.seen_cursor) == 1 and sleeps == []


def test_suppressed_count_survives_failed_send(tmp_path):
    relay, sender, clock, _, _ = make(tmp_path, window=600)
    relay.process(("c1", "alert=a"))
    clock.now = 10
    relay.process(("c2", "alert=a"))
    clock.now = 700
    sender.failures = [SendError(None, None, "OSError")] * 3
    relay.process(("c3", "alert=a"))
    relay.process(("c4", "alert=a"))
    assert sender.sent[-1] == "alert=a (+1 suppressed)"
