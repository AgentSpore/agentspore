"""start_with_evidence without Docker: call order and exception chaining."""

import pytest
from docker.errors import APIError

from tests.pg_evidence import start_with_evidence


class FakeWrapped:
    attrs = {"State": {"Status": "exited", "ExitCode": 1}}

    def __init__(self, calls):
        self.calls = calls

    def reload(self):
        self.calls.append("reload")

    def logs(self, tail):
        self.calls.append("logs")
        return b"boom"


class FakeContainer:
    def __init__(self, start_error, stop_error=None):
        self.calls = []
        self.start_error = start_error
        self.stop_error = stop_error

    def start(self):
        raise self.start_error

    def get_wrapped_container(self):
        return FakeWrapped(self.calls)

    def stop(self):
        self.calls.append("stop")
        if self.stop_error:
            raise self.stop_error


def test_start_failure_reads_evidence_then_stops():
    original = ValueError("not running")
    container = FakeContainer(original)
    with pytest.raises(RuntimeError, match="exit_code=1") as caught:
        start_with_evidence(container)
    assert container.calls == ["reload", "logs", "stop"]
    assert caught.value.__cause__ is original


def test_stop_failure_keeps_evidence_error():
    original = ValueError("not running")
    container = FakeContainer(original, stop_error=APIError("gone"))
    with pytest.raises(RuntimeError, match="last_logs='boom'") as caught:
        start_with_evidence(container)
    assert caught.value.__cause__ is original


def test_interrupt_stops_container_and_propagates():
    container = FakeContainer(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start_with_evidence(container)
    assert container.calls == ["stop"]
