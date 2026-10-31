"""Bounded subprocess runner for the trusted worker; never goes through a shell."""

import asyncio
import contextlib
import os
import signal
from pathlib import Path

from app.services.deployment.errors import DeployRejectedError

MAX_OUTPUT = 32 * 1024 * 1024
REAP_TIMEOUT = 5.0


async def _read_capped(stream: asyncio.StreamReader, name: str) -> bytes:
    buffer = bytearray()
    while chunk := await stream.read(65536):
        buffer += chunk
        if len(buffer) > MAX_OUTPUT:
            raise DeployRejectedError("tool_failed", f"{name}: output over {MAX_OUTPUT} bytes")
    return bytes(buffer)


async def run_tool(
    argv: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, bytes]:
    """Run `argv`, return (rc, stdout); timeout, missing binary, huge output: `tool_failed`.

    The tool runs in its own session so a timeout or overflow kills the whole process group.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(cwd), **(env or {})},
            start_new_session=True,
        )
    except OSError as exc:
        raise DeployRejectedError("tool_failed", f"{argv[0]}: {exc.strerror}") from exc
    assert proc.stdout is not None
    try:
        async with asyncio.timeout(timeout):
            stdout = await _read_capped(proc.stdout, argv[0])
            await proc.wait()
    except TimeoutError as exc:
        raise DeployRejectedError("tool_failed", f"{argv[0]}: timeout after {timeout:g}s") from exc
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), REAP_TIMEOUT)
    return proc.returncode or 0, stdout
