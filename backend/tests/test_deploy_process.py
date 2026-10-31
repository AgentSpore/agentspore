"""A4 P2a: run_tool bounds - process-group kill on timeout, streamed output cap."""

import asyncio
import os
import sys
import time

import pytest

from app.services.deployment import process
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.process import MAX_STDERR, redact, run_tool, run_tool_captured


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


async def test_overflow_with_paused_pipe_is_reaped_promptly(tmp_path, monkeypatch):
    # Slow consumer: the child floods the pipe, the stdout transport pauses with unread
    # data, then the cap fires. Process.wait() must not depend on seeing EOF there.
    monkeypatch.setattr(process, "MAX_OUTPUT", 1_000)
    read_capped = process._read_capped

    async def slow_read_capped(stream, name):
        await asyncio.sleep(0.3)
        return await read_capped(stream, name)

    monkeypatch.setattr(process, "_read_capped", slow_read_capped)
    pid_file = tmp_path / "pid"
    script = (
        "import os, sys, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "sys.stdout.buffer.write(b'x' * 4_000_000)\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    with pytest.raises(DeployRejectedError, match="output over"):
        await run_tool([sys.executable, "-c", script], cwd=tmp_path, timeout=20)
    assert time.monotonic() - started < 2
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


async def test_captured_returns_stderr_separately(tmp_path):
    out = await run_tool_captured(
        ["sh", "-c", "printf out; printf err >&2; exit 2"], cwd=tmp_path, timeout=5
    )
    assert (out.rc, out.stdout, out.stderr) == (2, b"out", b"err")


async def test_stderr_keeps_the_tail_within_cap_and_never_fails_the_run(tmp_path):
    script = "import sys\nsys.stderr.write('head\\n' + 'x' * 300_000 + '\\nTAIL-MARK')"
    out = await run_tool_captured([sys.executable, "-c", script], cwd=tmp_path, timeout=20)
    assert len(out.stderr) <= MAX_STDERR
    assert out.stderr.endswith(b"TAIL-MARK")
    assert b"head" not in out.stderr


async def test_stderr_flood_does_not_block_stdout(tmp_path):
    script = "import sys\nsys.stderr.write('e' * 2_000_000)\nsys.stdout.write('done')"
    out = await run_tool_captured([sys.executable, "-c", script], cwd=tmp_path, timeout=20)
    assert out.stdout == b"done"


async def test_credential_urls_are_redacted_on_both_streams(tmp_path):
    cmd = "echo 'https://u:pw-1@git.example/x'; echo 'fatal: git://tok@h/y' >&2"
    out = await run_tool_captured(["sh", "-c", cmd], cwd=tmp_path, timeout=5)
    assert b"pw-1" not in out.stdout and b"tok" not in out.stderr
    assert b"://***@git.example/x" in out.stdout and b"://***@h/y" in out.stderr


async def test_cut_stderr_never_leaves_a_credential_fragment(tmp_path):
    # The cap cuts inside the URL: the partial first line is dropped, so no password tail stays.
    pad = MAX_STDERR - len("fragment-secret@host/x\nend") - 8
    script = (
        "import sys\n"
        f"sys.stderr.write('https://user:very-long-' + 'z' * {pad}"
        " + 'fragment-secret@host/x\\nend')"
    )
    out = await run_tool_captured([sys.executable, "-c", script], cwd=tmp_path, timeout=20)
    assert b"fragment-secret" not in out.stderr


async def test_captured_timeout_is_tool_failed(tmp_path):
    with pytest.raises(DeployRejectedError, match="timeout"):
        await run_tool_captured(["sleep", "30"], cwd=tmp_path, timeout=0.3)


def test_redact_leaves_plain_text_alone():
    assert redact(b"no url here: a@b") == b"no url here: a@b"
