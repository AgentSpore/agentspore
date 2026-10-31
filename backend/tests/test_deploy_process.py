"""A4 P2a: run_tool bounds - process-group kill on timeout, streamed output cap."""

import os
import time

import pytest

from app.services.deployment import process
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.process import run_tool


async def test_run_tool_returns_rc_and_stdout(tmp_path):
    assert await run_tool(["sh", "-c", "printf hi; exit 3"], cwd=tmp_path, timeout=5) == (3, b"hi")


async def test_run_tool_missing_binary(tmp_path):
    with pytest.raises(DeployRejectedError) as exc:
        await run_tool(["definitely-not-a-binary"], cwd=tmp_path, timeout=5)
    assert exc.value.reason_code == "tool_failed"


async def test_timeout_kills_descendants_and_returns_promptly(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = f"sleep 30 & echo $! > {pidfile}; wait"
    started = time.monotonic()
    with pytest.raises(DeployRejectedError) as exc:
        await run_tool(["sh", "-c", script], cwd=tmp_path, timeout=0.5)
    assert exc.value.reason_code == "tool_failed"
    assert time.monotonic() - started < 4
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("grandchild survived the timeout")


async def test_output_over_cap_is_killed_while_streaming(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "MAX_OUTPUT", 100_000)
    started = time.monotonic()
    with pytest.raises(DeployRejectedError) as exc:
        await run_tool(["yes"], cwd=tmp_path, timeout=20)
    assert exc.value.reason_code == "tool_failed"
    assert "output over" in exc.value.detail
    assert time.monotonic() - started < 5
