"""Bounded subprocess runner for the trusted worker; never goes through a shell."""

import asyncio
import os
from pathlib import Path

from app.services.deployment.errors import DeployRejectedError

MAX_OUTPUT = 32 * 1024 * 1024


async def run_tool(
    argv: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> tuple[int, bytes]:
    """Run `argv`, return (rc, stdout); timeout, missing binary, huge output: `tool_failed`."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(cwd), **(env or {})},
            limit=MAX_OUTPUT,
        )
    except OSError as exc:
        raise DeployRejectedError("tool_failed", f"{argv[0]}: {exc.strerror}") from exc
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise DeployRejectedError("tool_failed", f"{argv[0]}: timeout after {timeout:g}s") from exc
    if len(stdout) >= MAX_OUTPUT:
        raise DeployRejectedError("tool_failed", f"{argv[0]}: output over {MAX_OUTPUT} bytes")
    return proc.returncode or 0, stdout
