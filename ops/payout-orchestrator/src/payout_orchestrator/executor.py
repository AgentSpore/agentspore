"""Process seam: container CLIs through `docker exec`, the signer through systemd.

The backend CLIs use different exit-code tables (payout_delivery, welcome_payouts and the
signer all disagree), so callers branch on the JSON `verdict` field, never on an exit code.
The signer's JSON verdict goes to the systemd journal, so only its ExecMainStatus is available
here; it is trusted only when systemd reports a normal exit (ExecMainCode=1).
"""

import json
import os
import re
import subprocess  # noqa: S404 (docker and systemctl are the purpose; argv lists, no shell)
from pathlib import Path
from typing import Protocol

VERDICT = re.compile(r"[a-z_]{1,32}")


class ExecutorError(Exception):
    """A step timed out, failed to start or printed no usable verdict; carries no output."""


class Executor(Protocol):
    def cli(self, module: str, args: list[str], stdin: str = "") -> dict: ...

    def fetch(self, container_path: str, host_path: Path) -> None: ...

    def drop(self, container_path: str) -> None: ...

    def sign(self, instance: str) -> int: ...

    def block_height(self) -> int | None:
        """Chain height, or None when it cannot be proven (callers fail closed)."""
        ...


def parse_verdict(raw: bytes) -> dict:
    """Read the last stdout line as one JSON object with a short lowercase `verdict`."""
    lines = raw.decode(errors="replace").strip().splitlines()
    try:
        body = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise ExecutorError("no verdict") from None
    verdict = body.get("verdict") if isinstance(body, dict) else None
    if not isinstance(verdict, str) or not VERDICT.fullmatch(verdict):
        raise ExecutorError("bad verdict")
    return body


def write_private(path: Path, data: bytes) -> None:
    """Write 0640 through a temp file so a crash never leaves a truncated handoff file."""
    temp = path.with_name(path.name + ".part")
    temp.unlink(missing_ok=True)
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class DockerExecutor:
    """Real Executor: `docker exec` for the backend CLIs, `systemctl` for the signer unit."""

    def __init__(self, container: str, timeout: int) -> None:
        self.container = container
        self.timeout = timeout

    def _run(self, argv: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(  # noqa: S603 (fixed argv list; intent ids are validated UUIDs, epochs ints)
                argv, input=stdin, capture_output=True, timeout=self.timeout, check=False
            )
        except (subprocess.TimeoutExpired, OSError):
            raise ExecutorError("step failed") from None

    def _exec(self, *argv: str) -> list[str]:
        return ["docker", "exec", "-i", self.container, *argv]

    def cli(self, module: str, args: list[str], stdin: str = "") -> dict:
        argv = self._exec("python", "-m", f"app.cli.{module}", *args)
        return parse_verdict(self._run(argv, stdin.encode()).stdout)

    def fetch(self, container_path: str, host_path: Path) -> None:
        done = self._run(self._exec("cat", container_path))
        if done.returncode != 0 or not done.stdout:
            raise ExecutorError("fetch failed")
        write_private(host_path, done.stdout)

    def drop(self, container_path: str) -> None:
        self._run(self._exec("rm", "-f", container_path))

    def sign(self, instance: str) -> int:
        unit = f"payout-signer@{instance}.service"
        self._run(["systemctl", "start", unit])  # failing units exit non-zero by design
        properties = "ExecMainCode,ExecMainStatus,Result"
        shown = self._run(["systemctl", "show", "-p", properties, unit])
        props = dict(
            line.split("=", 1) for line in shown.stdout.decode().splitlines() if "=" in line
        )
        # a unit that never ran or died on a signal reports status 0 with a different code
        if props.get("ExecMainCode") != "1" or props.get("Result") not in ("success", "exit-code"):
            raise ExecutorError("no signer status")
        try:
            return int(props["ExecMainStatus"])
        except (KeyError, ValueError):
            raise ExecutorError("no signer status") from None

    def block_height(self) -> int | None:
        """Finalized chain height from the backend `block-height` command; None when unproven."""
        try:
            body = self.cli("payout_delivery", ["block-height"], "{}")
        except ExecutorError:
            return None
        height = body.get("height")
        if body["verdict"] != "found" or type(height) is not int:
            return None
        return height
