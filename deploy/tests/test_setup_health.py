"""Exercise only setup.sh's health-decision fragment, never the full installer."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def health_fragment() -> str:
    """Reject installer operations before running the source health fragment."""
    source = (Path(__file__).resolve().parents[1] / "setup.sh").read_text()
    marker = "# 5. Wait and verify."
    assert source.count(marker) == 1
    fragment = source[source.index(marker) :]
    assert not re.search(
        r"(?m)^\s*(?:sudo|ufw|fallocate|chmod|mkswap|swapon|rm|cp|git|cd)\b"
        r"|/etc/|/swapfile|docker compose[^\n]*\bup\b",
        fragment,
    )
    allowed = ("#", "echo ", "HEALTHY=", "for _ in ", "if ", "sleep ")
    for line in fragment.splitlines():
        statement = line.strip()
        assert (
            not statement
            or statement.startswith(allowed)
            or statement in {"break", "fi", "done", "else", "exit 1"}
        )
    assert re.findall(r"\$\(([^)]+)\)", fragment) == ["seq 1 12", "curl -s ifconfig.me"]
    return fragment


@pytest.fixture
def stub_path(tmp_path: Path) -> Path:
    """Supply only the outward commands and the real local seq executable."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    scripts = {
        "docker": """#!/bin/bash
set -euo pipefail
[[ $# -eq 5 && $1 = exec && $2 = agentspore-backend && $3 = curl ]] || exit 99
[[ $4 = -sf && $5 = http://localhost:8000/health ]] || exit 99
read -r attempts < "$COUNT"
attempts=$((attempts + 1))
printf '%s\\n' "$attempts" > "$COUNT"
if [[ $PROBE_CODE -eq 125 ]]; then exit 125; fi
if [[ $HEALTHY_AT -gt 0 && $attempts -ge $HEALTHY_AT ]]; then exit 0; fi
exit "$PROBE_CODE"
""",
        "curl": """#!/bin/bash
set -euo pipefail
[[ $# -eq 2 && $1 = -s && $2 = ifconfig.me ]] || exit 99
printf '203.0.113.1\\n'
""",
        "sleep": """#!/bin/bash
set -euo pipefail
[[ $# -eq 1 && $1 = 5 ]] || exit 99
""",
    }
    for name, script in scripts.items():
        command = binaries / name
        command.write_text(script)
        command.chmod(0o700)
    seq = shutil.which("seq")
    assert seq is not None
    (binaries / "seq").symlink_to(seq)
    return binaries


@pytest.mark.parametrize(
    "scenario",
    [(1, 22, 1, 0), (3, 22, 3, 0), (0, 22, 12, 1), (0, 125, 12, 1)],
    ids=["immediate-healthy", "eventual-healthy", "unhealthy", "docker-error"],
)
def test_setup_health_decision(
    tmp_path: Path,
    health_fragment: str,
    stub_path: Path,
    scenario: tuple[int, int, int, int],
) -> None:
    """Run the unmodified Bash decision against controlled boundary responses."""
    healthy_at, probe_code, expected_attempts, expected_exit = scenario
    count = tmp_path / "attempts"
    count.write_text("0\n")
    result = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-euo",
            "pipefail",
            "-c",
            health_fragment,
        ],
        env={
            "PATH": str(stub_path),
            "COUNT": str(count),
            "HEALTHY_AT": str(healthy_at),
            "PROBE_CODE": str(probe_code),
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )
    assert int(count.read_text()) == expected_attempts
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert ("=== SUCCESS ===" in result.stdout) == (expected_exit == 0)
    assert ("WARNING: Backend health check failed." in result.stdout) == (expected_exit == 1)
    assert result.stderr == ""
