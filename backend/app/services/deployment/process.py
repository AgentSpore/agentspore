"""Bounded subprocess runner for the trusted worker; never goes through a shell."""

import asyncio
import contextlib
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path

from app.services.deployment.errors import DeployRejectedError

MAX_OUTPUT = 32 * 1024 * 1024
MAX_STDERR = 64 * 1024
REAP_TIMEOUT = 5.0
# Every pattern is anchored on a literal and bounded, so none can rescan a long input.
_HEADER = re.compile(
    rb"(?i)((?:authorization|private-token|x-auth-token|set-cookie|cookie)[ \t]?[:=][ \t]?)"
    rb"[^\r\n]{1,512}"
)
_BEARER = re.compile(rb"(?i)\b(bearer\s{1,16})[A-Za-z0-9._~+/=-]{1,2048}")
_PAIR = re.compile(rb"(?i)((?:token|password|passwd|secret|api_?key)=)[^\s&\"']{1,512}")
_PASSWORD = re.compile(rb"(?i)(password\"?[ \t]*:[ \t]*\"?|--pass(?:word)?[ \t]+)[^\s\",}]{1,512}")
_BARE = re.compile(rb"(?:gh[pousr]_|glpat-)[A-Za-z0-9_-]{20,512}|AKIA[0-9A-Z]{16}")
_TOKEN = re.compile(rb"\S+")


@dataclass(frozen=True)
class ToolOutput:
    rc: int
    stdout: bytes
    stderr: bytes


def _mask_userinfo(match: re.Match[bytes]) -> bytes:
    """Mask `scheme://user:pass@host` and scheme-less `user:pass@host` up to the LAST `@`, so a
    password holding `/` or `@` leaks no tail. One whitespace-free token at a time: linear."""
    token = match.group()
    at = token.rfind(b"@")
    while at >= 0 and token.startswith(b"sha256:", at + 1):  # an image digest, not userinfo
        at = token.rfind(b"@", 0, at)
    if at < 0:
        return token
    scheme = token.find(b"://")
    if scheme >= 0:
        return token[: scheme + 3] + b"***" + token[at:]
    colon = token.find(b":")
    return b"***" + token[at:] if 0 <= colon < at else token


def redact(data: bytes) -> bytes:
    """Mask URL and `user:pass@` credentials, credential headers, `Bearer` values, password and
    `token|secret|api_key=` pairs and bare GitHub/GitLab/AWS keys. For text people read (stderr,
    failure details), never for stdout a caller parses: JSON can come out invalid."""
    data = _HEADER.sub(rb"\1***", data)
    data = _BEARER.sub(rb"\1***", data)
    data = _PAIR.sub(rb"\1***", data)
    data = _PASSWORD.sub(rb"\1***", data)
    data = _BARE.sub(b"***", data)
    return _TOKEN.sub(_mask_userinfo, data)


async def _read_tail(stream: asyncio.StreamReader) -> bytes:
    """Keep the last `MAX_STDERR` bytes without ever failing the run.

    A cut drops the partial first line too: a URL credential cannot span lines, so no password
    tail survives without its `://`.
    """
    buffer = bytearray()
    cut = False
    while chunk := await stream.read(65536):
        buffer += chunk
        if len(buffer) > MAX_STDERR:
            del buffer[:-MAX_STDERR]
            cut = True
    if cut:
        _, newline, rest = bytes(buffer).partition(b"\n")
        return rest if newline else b""
    return bytes(buffer)


async def _read_capped(stream: asyncio.StreamReader, name: str) -> bytes:
    buffer = bytearray()
    while chunk := await stream.read(65536):
        buffer += chunk
        if len(buffer) > MAX_OUTPUT:
            raise DeployRejectedError("tool_failed", f"{name}: output over {MAX_OUTPUT} bytes")
    return bytes(buffer)


async def _reap(proc: asyncio.subprocess.Process, *streams: asyncio.StreamReader) -> None:
    """Wait for exit; `Process.wait()` also needs the pipes at EOF (base_subprocess.py
    `_try_finish`), and a paused transport only resumes when its reader is drained."""
    await asyncio.gather(proc.wait(), *(_drain(stream) for stream in streams))


async def _drain(stream: asyncio.StreamReader) -> None:
    while await stream.read(65536):
        pass


async def run_tool(
    argv: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, bytes]:
    """`run_tool_captured` without stderr, for callers that only parse stdout."""
    out = await run_tool_captured(argv, cwd=cwd, timeout=timeout, env=env)
    return out.rc, out.stdout


async def run_tool_captured(
    argv: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> ToolOutput:
    """Run `argv`; timeout, missing binary, huge stdout: `tool_failed`.

    stdout is returned raw, capped at `MAX_OUTPUT`; stderr keeps its last `MAX_STDERR` bytes,
    redacted. The tool runs in its own session so a timeout or overflow kills the process group.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(cwd), **(env or {})},
            start_new_session=True,
        )
    except OSError as exc:
        raise DeployRejectedError("tool_failed", f"{argv[0]}: {exc.strerror}") from exc
    assert proc.stdout is not None and proc.stderr is not None
    err_task = asyncio.create_task(_read_tail(proc.stderr))
    try:
        async with asyncio.timeout(timeout):
            stdout = await _read_capped(proc.stdout, argv[0])
            await proc.wait()
            stderr = await err_task
    except TimeoutError as exc:
        raise DeployRejectedError("tool_failed", f"{argv[0]}: timeout after {timeout:g}s") from exc
    finally:
        err_task.cancel()  # its reader must be free before `_reap` drains the pipe
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(err_task, return_exceptions=True)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_reap(proc, proc.stdout, proc.stderr), REAP_TIMEOUT)
    return ToolOutput(proc.returncode or 0, stdout, redact(stderr))
