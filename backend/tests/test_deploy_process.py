"""A4 P2a: run_tool bounds - process-group kill on timeout, streamed output cap."""

import asyncio
import json
import os
import sys
import time
from typing import cast

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


async def test_stderr_keeps_exactly_the_last_64k_and_never_fails_the_run(tmp_path):
    # Short lines all through: a wrong cap changes the kept size, not just the final marker.
    script = "import sys\nsys.stderr.write(('x' * 99 + '\\n') * 3000 + 'TAIL-MARK')"
    out = await run_tool_captured([sys.executable, "-c", script], cwd=tmp_path, timeout=20)
    assert 64 * 1024 - 100 <= len(out.stderr) <= 64 * 1024  # literal: the constant is under test
    assert out.stderr.endswith(b"TAIL-MARK")


async def test_stderr_is_redacted_and_stdout_is_returned_raw(tmp_path):
    cmd = "echo 'https://u:pw-1@git.example/x'; echo 'fatal: git://tok@h/y' >&2"
    out = await run_tool_captured(["sh", "-c", cmd], cwd=tmp_path, timeout=5)
    assert b"tok" not in out.stderr and b"://***@h/y" in out.stderr
    assert out.stdout == b"https://u:pw-1@git.example/x\n"


async def test_json_stdout_round_trips_unchanged(tmp_path):
    # scans.py json.loads scanner stdout: credential-like words in advisories must survive.
    doc = {
        "details": "sends Authorization:\nnext line, password: x, user:pw@host, token=abc",
        "refs": ["https://u:p@h/x", "ghp_" + "a" * 30, "Cookie: sid=1"],
    }
    path = tmp_path / "osv.json"
    path.write_text(json.dumps(doc))
    captured = await run_tool_captured(["cat", str(path)], cwd=tmp_path, timeout=5)
    _, plain = await run_tool(["cat", str(path)], cwd=tmp_path, timeout=5)
    for raw in (captured.stdout, plain):
        assert raw == path.read_bytes() and json.loads(raw) == doc


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


async def test_reap_drains_every_pipe_it_is_given():
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    for stream in (stdout, stderr):
        stream.feed_data(b"unread" * 50_000)
        stream.feed_eof()

    class Exited:
        async def wait(self):
            return 0

    await process._reap(cast(asyncio.subprocess.Process, Exited()), stdout, stderr)
    assert await stdout.read(1) == b"" and await stderr.read(1) == b""


async def test_run_tool_reaps_with_both_stdout_and_stderr(tmp_path, monkeypatch):
    seen = []
    reap = process._reap

    async def spy(proc, *streams):
        seen.append(len(streams))
        await reap(proc, *streams)

    monkeypatch.setattr(process, "_reap", spy)
    await run_tool_captured(["true"], cwd=tmp_path, timeout=5)
    assert seen == [2]


@pytest.mark.parametrize(
    ("raw", "gone", "kept"),
    [
        (b"git clone https://u:pw1@h/x", b"pw1", b"://***@h/x"),
        (b"fatal: https://u:p/w@ss@host/path", b"w@ss", b"@host/path"),
        (b"dial u:hunter2@db.local:5432 failed", b"hunter2", b"@db.local:5432"),
        (b"Authorization: Basic dXNlcjpwdw==", b"dXNlcjpwdw", b"Authorization:"),
        (b"authorization=Bearer abc.def", b"abc.def", b"authorization="),
        (b"curl -H 'Bearer sk-123_abc'", b"sk-123_abc", b"Bearer"),
        (b"GET /x?access_token=ghs_abc&y=1", b"ghs_abc", b"access_token=***&y=1"),
        (b"env GITHUB_TOKEN=tok123 PASSWORD=pw9 api_key=k1", b"tok123", b"GITHUB_TOKEN=***"),
        (b"a secret=top\nnext line", b"top", b"next line"),
        (b"password: hunter2 rest", b"hunter2", b"rest"),
        (b'{"password":"hunter2","a":1}', b"hunter2", b'"a":1'),
        (b"mysql --password hunter2 -u x", b"hunter2", b"-u x"),
        (b"PRIVATE-TOKEN: glx9", b"glx9", b"PRIVATE-TOKEN:"),
        (b"X-Auth-Token: t0k3n", b"t0k3n", b"X-Auth-Token:"),
        (b"Cookie: sid=abc; theme=dark", b"sid=abc", b"Cookie:"),
        (b"push ghp_" + b"A" * 36 + b" failed", b"A" * 36, b" failed"),
        (b"key glpat-" + b"b" * 22 + b" bad", b"b" * 22, b" bad"),
        (b"id AKIA" + b"ABCDEFGHIJKLMNOP end", b"ABCDEFGHIJKLMNOP", b" end"),
    ],
)
def test_redact_masks_every_credential_shape(raw, gone, kept):
    out = redact(raw)
    assert gone not in out and kept in out


def test_redact_masks_up_to_the_last_at_exactly():
    assert redact(b"fatal: https://u:p/w@ss@host/path") == b"fatal: https://***@host/path"
    assert redact(b"dial u:p@w@db:5432") == b"dial ***@db:5432"


def test_header_at_end_of_line_leaves_the_next_line_alone():
    assert (
        redact(b"sent Authorization:\nnext line stays") == b"sent Authorization:\nnext line stays"
    )


def test_credentials_before_a_digest_are_still_masked():
    ref = b"https://u:pw@h/app@sha256:" + b"ef" * 32
    assert redact(ref) == b"https://***@h/app@sha256:" + b"ef" * 32


@pytest.mark.parametrize(
    "text",
    [
        b"a@b.com wrote: hi time 12:30 see https://example.org/a/b x=1&y=2",
        b"pulling registry.local:5000/team/app@sha256:" + b"ab" * 32,
        b"app:1.2@sha256:" + b"cd" * 32 + b" not found",
    ],
)
def test_redact_keeps_harmless_text_and_image_digests(text):
    assert redact(text) == text


@pytest.mark.parametrize(
    "unit",
    [
        u.encode()
        for u in "://,Bearer ,authorization:,token=,a:b@,password:,--password ,Cookie:".split(",")
    ]
    + [b"x" * 100, b"@" * 50 + b":", b"ghp_", b"AKIA", b"a:b@sha256:"],
)
def test_redact_is_linear_on_adversarial_input(unit):
    data = unit * (1_000_000 // len(unit))
    started = time.monotonic()
    redact(data)
    assert time.monotonic() - started < 1
