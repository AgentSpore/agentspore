import logging
import threading

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


def test_stop_during_retry_wait_ends_retries_and_keeps_cursor(tmp_path):
    relay, sender, _, _, cursor = make(tmp_path, failures=[SendError(500, None, "HTTPError")] * 3)
    relay._sleep = lambda seconds: True
    cursor.write_text("old")
    relay.process(("c1", "alert=a"))
    assert len(sender.seen_cursor) == 1 and cursor.read_text() == "old"


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


def test_marker_writes_cursor_without_sending(tmp_path):
    relay, sender, _, _, cursor = make(tmp_path)
    relay.process(("m1", None))
    assert sender.sent == [] and cursor.read_text() == "m1"


def test_marker_after_alert_keeps_cursor_order(tmp_path):
    relay, _, _, _, cursor = make(tmp_path)
    relay.submit(("c1", "alert=a"))
    relay.submit(("m2", None))
    for _ in range(2):
        relay.process(relay._queue.get_nowait())
    assert cursor.read_text() == "m2"


def test_full_queue_discards_marker_and_keeps_dropped_count(tmp_path):
    relay, _, _, _, _ = make(tmp_path, queue_size=2)
    relay.submit(("c1", "alert=a"))
    relay.submit(("c2", "alert=b"))
    relay.submit(("m3", None))
    assert [relay._queue.get_nowait()[0] for _ in range(2)] == ["c1", "c2"]
    assert relay._dropped == 0


def test_alert_evicting_marker_is_not_counted_as_dropped(tmp_path):
    relay, sender, _, _, _ = make(tmp_path, queue_size=1)
    relay.submit(("m1", None))
    relay.submit(("c2", "alert=a"))
    relay.process(relay._queue.get_nowait())
    assert sender.sent == ["alert=a"]
    assert relay._dropped == 0


def test_marker_keeps_cursor_while_an_alert_was_evicted(tmp_path):
    relay, _, _, _, cursor = make(tmp_path, queue_size=2)
    cursor.write_text("old")
    relay.submit(("a1", "alert=a"))
    relay.submit(("m2", None))
    relay.submit(("b3", "alert=b"))
    relay.process(relay._queue.get_nowait())
    assert cursor.read_text() == "old"


def test_stop_during_retry_wait_leaves_cursor_behind_a_queued_marker(tmp_path):
    stop, cursor = threading.Event(), tmp_path / "cursor"
    cursor.write_text("old")

    def sleep(seconds):
        stop.set()
        return stop.wait(seconds)

    sender = Sender(cursor, [SendError(500, None, "HTTPError")])
    relay = Relay(sender.send, cursor, RelayOptions(), sleep=sleep)
    relay.submit(("c1", "alert=a"))
    relay.submit(("m2", None))
    relay.run(stop)
    assert sender.sent == [] and cursor.read_text() == "old"
