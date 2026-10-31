"""Pre-build gate: gitleaks, osv-scanner and the Dockerfile `FROM` allowlist.

Findings carry rule id, file and line only: scanner output is never forwarded verbatim,
so a leaked secret value cannot reach logs or `failure detail`.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.process import run_tool

TOOL_TIMEOUT = 300.0
CRITICAL_CVSS = 9.0
Runner = Callable[..., Awaitable[tuple[int, bytes]]]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    file: str
    line: int
    reason_code: str
    blocking: bool = True


class Scanner(Protocol):
    async def scan(self, root: Path) -> list[Finding]: ...


def _json(stdout: bytes, tool: str) -> object:
    try:
        return json.loads(stdout) if stdout.strip() else None
    except ValueError as exc:
        raise DeployRejectedError("tool_failed", f"{tool}: unreadable report") from exc


class GitleaksScanner:
    """`gitleaks dir`: exit 0 clean, 1 leaks (JSON on stdout), anything else is a tool failure."""

    def __init__(self, run: Runner = run_tool):
        self.run = run

    async def scan(self, root: Path) -> list[Finding]:
        argv = ["gitleaks", "dir", ".", "--report-format", "json", "--report-path", "-",
                "--no-banner", "--redact", "--exit-code", "1"]  # fmt: skip
        rc, out = await self.run(argv, cwd=root, timeout=TOOL_TIMEOUT)
        if rc not in (0, 1):
            raise DeployRejectedError("tool_failed", f"gitleaks exited {rc}")
        report = _json(out, "gitleaks") or []
        if not isinstance(report, list):
            raise DeployRejectedError("tool_failed", "gitleaks: unexpected report shape")
        return [
            Finding(str(r["RuleID"]), str(r["File"]), int(r["StartLine"]), "secret_found")
            for r in report
        ]


class OsvScanner:
    """`osv-scanner scan source`: exit 0 clean, 1 vulnerable, 128 no packages; CVSS >= 9 blocks."""

    def __init__(self, run: Runner = run_tool):
        self.run = run

    async def scan(self, root: Path) -> list[Finding]:
        argv = ["osv-scanner", "scan", "source", "--recursive", "--format", "json", "."]
        rc, out = await self.run(argv, cwd=root, timeout=TOOL_TIMEOUT)
        if rc == 128:
            return []
        if rc not in (0, 1):
            raise DeployRejectedError("tool_failed", f"osv-scanner exited {rc}")
        report = _json(out, "osv-scanner") or {}
        findings = []
        for result in report.get("results", []) if isinstance(report, dict) else []:
            lockfile = self._relative(result.get("source", {}).get("path", ""), root)
            for package in result.get("packages", []):
                scores = self._scores(package.get("groups", []))
                for vuln in package.get("vulnerabilities", []):
                    score = scores.get(vuln["id"], 0.0)
                    findings.append(
                        Finding(
                            vuln["id"], lockfile, 0, "dependency_vulnerable", score >= CRITICAL_CVSS
                        )
                    )
        return findings

    @staticmethod
    def _scores(groups: list[dict]) -> dict[str, float]:
        scores = {}
        for group in groups:
            try:
                score = float(group.get("max_severity", ""))
            except ValueError:
                continue
            scores.update({vuln_id: score for vuln_id in group.get("ids", [])})
        return scores

    @staticmethod
    def _relative(path: str, root: Path) -> str:
        try:
            return Path(path).relative_to(root).as_posix()
        except ValueError:
            return Path(path).name


_SYNTAX = re.compile(r"^#\s*syntax\s*=", re.IGNORECASE)
_FROM = re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
_COPY_FROM = re.compile(r"^COPY\s+.*?--from=(\S+)", re.IGNORECASE)
_DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})$")


class DockerfileScanner:
    """Base images only by allowlisted digest; local stages and `scratch` ok; no `# syntax=`."""

    def __init__(self, allowed_digests: frozenset[str], dockerfile: str = "Dockerfile"):
        self.allowed = allowed_digests
        self.dockerfile = dockerfile

    def _allowed(self, ref: str, stages: set[str]) -> bool:
        if ref.lower() in stages or ref.lower() == "scratch" or ref.isdigit():
            return True
        match = _DIGEST.search(ref)
        return match is not None and match.group(1) in self.allowed

    async def scan(self, root: Path) -> list[Finding]:
        path = root / self.dockerfile
        if not path.is_file():
            return [Finding("dockerfile-missing", self.dockerfile, 0, "dockerfile_missing")]
        findings, stages = [], set()
        for number, line in self._logical_lines(path.read_text(errors="replace")):
            rule = self._violation(line, stages)
            if rule:
                findings.append(Finding(rule, self.dockerfile, number, "base_not_allowed"))
        return findings

    def _violation(self, line: str, stages: set[str]) -> str | None:
        if _SYNTAX.match(line):
            return "syntax-directive"
        if (match := _FROM.match(line)) is not None:
            ok = self._allowed(match.group(1), stages)
            if match.group(2):
                stages.add(match.group(2).lower())
            return None if ok else "from-not-allowed"
        if (match := _COPY_FROM.match(line)) is not None and not self._allowed(
            match.group(1), stages
        ):
            return "from-not-allowed"
        return None

    @staticmethod
    def _logical_lines(text: str) -> list[tuple[int, str]]:
        """Join backslash continuations; number is the first physical line."""
        out, buffer, start = [], "", 0
        for number, raw in enumerate(text.splitlines(), 1):
            stripped = raw.strip()
            if stripped.startswith("#") and not buffer:
                out.append((number, stripped))
                continue
            start = start or number
            buffer += " " + stripped.removesuffix("\\")
            if not stripped.endswith("\\"):
                out.append((start, buffer.strip()))
                buffer, start = "", 0
        return out + ([(start, buffer.strip())] if buffer else [])


async def run_prescan(root: Path, scanners: list[Scanner]) -> list[Finding]:
    """Run every scanner, log findings without values, refuse on the first blocking one."""
    findings = [f for scanner in scanners for f in await scanner.scan(root)]
    for f in findings:
        logger.warning(
            "deploy scan finding rule={} file={} line={} blocking={}",
            f.rule_id,
            f.file,
            f.line,
            f.blocking,
        )
    blocking = next((f for f in findings if f.blocking), None)
    if blocking is not None:
        raise DeployRejectedError(
            blocking.reason_code, f"{blocking.rule_id} at {blocking.file}:{blocking.line}"
        )
    return findings
