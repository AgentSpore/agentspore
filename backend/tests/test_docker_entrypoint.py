"""UVICORN_WORKERS contract of backend/docker-entrypoint.sh and the Dockerfile.

uvicorn reads UVICORN_WORKERS itself (click auto_envvar_prefix) but treats 0 as
"1" and gives no clear message for other junk, so the entrypoint validates
first and fails loudly instead of silently falling back.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENTRYPOINT = BACKEND_DIR / "docker-entrypoint.sh"
DOCKERFILE = BACKEND_DIR / "Dockerfile"


def _run(workers: str | None, *command: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "UVICORN_WORKERS"}
    if workers is not None:
        env["UVICORN_WORKERS"] = workers
    return subprocess.run(
        ["sh", str(ENTRYPOINT), *command], env=env, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    ("given", "expected"), [(None, "4"), ("", "4"), ("1", "1"), ("2", "2"), ("16", "16")]
)
def test_valid_or_unset_value_reaches_the_command(given: str | None, expected: str) -> None:
    result = _run(given, "sh", "-c", 'printf "%s" "$UVICORN_WORKERS"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.parametrize("bad", ["0", "00", "02", "-1", "abc", "2.5", " 2", "2 ", "1e3"])
def test_invalid_value_fails_loudly_and_never_runs_the_command(bad: str) -> None:
    result = _run(bad, "echo", "started")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "UVICORN_WORKERS" in result.stderr
    assert bad in result.stderr


def test_command_is_exec_ed_so_it_stays_pid_one() -> None:
    result = _run(None, "sh", "-c", 'printf "%s" "$PPID"')
    # exec replaces the entrypoint shell: the command's parent is this test, not an intermediate sh.
    assert result.stdout == str(os.getpid())


def test_dockerfile_wires_entrypoint_and_leaves_workers_to_the_env() -> None:
    text = re.sub(r"\\\s*\n\s*", " ", DOCKERFILE.read_text())
    assert re.search(r'^ENTRYPOINT\s+\["/app/docker-entrypoint\.sh"\]', text, re.MULTILINE)
    copies = "".join(re.findall(r"^COPY\s+.+$", text, re.MULTILINE))
    assert "docker-entrypoint.sh" in copies
    cmd = re.search(r"^CMD\s+(.+)$", text, re.MULTILINE)
    assert cmd, "backend/Dockerfile has no CMD"
    assert "--workers" not in cmd.group(1), "a literal --workers beats UVICORN_WORKERS"
