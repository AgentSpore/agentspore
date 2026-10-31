"""Pre-build gate: gitleaks, osv-scanner and the Dockerfile `FROM` allowlist.

Findings carry rule id, file and line only: scanner output is never forwarded verbatim,
so a leaked secret value cannot reach logs or `failure detail`. The scanned tree is
untrusted, so each tool runs on platform-owned config and every unreadable or
unexpected report is a `tool_failed`, never a pass.
"""

import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.process import run_tool

TOOL_TIMEOUT = 300.0
CRITICAL_CVSS = 9.0
GITLEAKS_FOUND = 42  # gitleaks fatal errors exit 1, so "leaks found" gets its own code
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


def _report(stdout: bytes, tool: str) -> object:
    try:
        return json.loads(stdout)
    except ValueError as exc:
        raise DeployRejectedError("tool_failed", f"{tool}: unreadable report") from exc


def _bad_shape(tool: str) -> DeployRejectedError:
    return DeployRejectedError("tool_failed", f"{tool}: unexpected report shape")


class GitleaksScanner:
    """`gitleaks dir` on platform config; repo allowlists and `gitleaks:allow` are ignored."""

    def __init__(self, run: Runner = run_tool):
        self.run = run

    async def scan(self, root: Path) -> list[Finding]:
        if (root / ".gitleaksignore").exists():  # gitleaks always reads it from the scanned root
            return [Finding("gitleaksignore-present", ".gitleaksignore", 0, "source_unsafe")]
        with tempfile.TemporaryDirectory() as platform:
            config, ignore = Path(platform) / "gitleaks.toml", Path(platform) / "gitleaksignore"
            config.write_text("[extend]\nuseDefault = true\n")
            ignore.write_text("")
            argv = ["gitleaks", "dir", ".", "--config", str(config),
                    "--gitleaks-ignore-path", str(ignore), "--ignore-gitleaks-allow",
                    "--report-format", "json", "--report-path", "-",
                    "--no-banner", "--redact", "--exit-code", str(GITLEAKS_FOUND)]  # fmt: skip
            rc, out = await self.run(argv, cwd=root, timeout=TOOL_TIMEOUT)
        if rc not in (0, GITLEAKS_FOUND):
            raise DeployRejectedError("tool_failed", f"gitleaks exited {rc}")
        report = _report(out, "gitleaks")
        if not isinstance(report, list) or (rc == GITLEAKS_FOUND and not report):
            raise _bad_shape("gitleaks")
        try:
            return [
                Finding(str(r["RuleID"]), str(r["File"]), int(r["StartLine"]), "secret_found")
                for r in report
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise _bad_shape("gitleaks") from exc


class OsvScanner:
    """`osv-scanner scan source` on an empty platform config; only CVSS >= 9 blocks."""

    def __init__(self, run: Runner = run_tool):
        self.run = run

    async def scan(self, root: Path) -> list[Finding]:
        with tempfile.TemporaryDirectory() as platform:
            config = Path(platform) / "osv-scanner.toml"
            config.write_text("")
            argv = ["osv-scanner", "scan", "source", f"--config={config}", "--recursive",
                    "--format", "json", "."]  # fmt: skip
            rc, out = await self.run(argv, cwd=root, timeout=TOOL_TIMEOUT)
        if rc in (0, 128):  # 128: no packages found
            return []
        if rc != 1:
            raise DeployRejectedError("tool_failed", f"osv-scanner exited {rc}")
        report = _report(out, "osv-scanner")
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("results"), list)
            or not report["results"]
        ):
            raise _bad_shape("osv-scanner")
        try:
            return self._findings(report["results"], root)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise _bad_shape("osv-scanner") from exc

    def _findings(self, results: list[dict], root: Path) -> list[Finding]:
        findings = []
        for result in results:
            lockfile = self._relative(result["source"]["path"], root)
            for package in result["packages"]:
                scores = self._scores(package.get("groups", []))
                for vuln in package["vulnerabilities"]:
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


_DIRECTIVE = re.compile(r"^#\s*(syntax|escape|check)\s*=", re.IGNORECASE)
_FROM = re.compile(r"^FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
_COPY_FROM = re.compile(r"^COPY\s+.*?--from=(\S+)", re.IGNORECASE)
_FLAG_FORBIDDEN = re.compile(r"""['"\\$]""")
_MOUNT_KEYS = frozenset(
    "type target dst destination id sharing mode uid gid required size "
    "ro readonly rw source src".split()
)
_MOUNT_BARE = frozenset({"required", "ro", "readonly", "rw"})
_MOUNT_TYPES = frozenset({"cache", "tmpfs", "secret", "bind", "ssh"})
_MOUNT_VALUE = re.compile(r"[A-Za-z0-9_./:@-]+")
_UNSUPPORTED = "dockerfile-unsupported"
_DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})$")


class DockerfileScanner:
    """Base images only by allowlisted digest; local stages and `scratch` ok; no parser directives.

    Directives (`syntax`, `escape`, `check`) are refused outright: BuildKit would read the
    file with a different escape character than this scanner and see FROM lines it cannot.
    Instruction flags are an allowlist grammar (no quotes, escapes or `$`; fixed `--mount` keys),
    never a blacklist of spellings. Defense in depth: the registry-mirror allowlist (P3) is the
    real boundary for which images a build can pull.
    """

    def __init__(self, allowed_digests: frozenset[str], dockerfile: str = "Dockerfile"):
        self.allowed = allowed_digests
        self.dockerfile = dockerfile

    def _allowed(self, ref: str, stages: set[str], *, stage_index: bool = False) -> bool:
        if ref.lower() in stages or ref.lower() == "scratch" or (stage_index and ref.isdigit()):
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
        if (directive := _DIRECTIVE.match(line)) is not None:
            return (
                "syntax-directive" if directive.group(1).lower() == "syntax" else "parser-directive"
            )
        if rule := self._flag_violation(line, stages):
            return rule
        if (match := _FROM.match(line)) is not None:
            ok = self._allowed(match.group(1), stages)
            if match.group(2):
                stages.add(match.group(2).lower())
            return None if ok else "from-not-allowed"
        if (match := _COPY_FROM.match(line)) is not None and not self._allowed(
            match.group(1), stages, stage_index=True
        ):
            return "from-not-allowed"
        return None

    def _flag_violation(self, line: str, stages: set[str]) -> str | None:
        for word in self._flags(line):
            if _FLAG_FORBIDDEN.search(word):
                return _UNSUPPORTED
            if word.lower().startswith("--mount"):
                rule = self._mount_violation(word, stages)
                if rule:
                    return rule
        return None

    @staticmethod
    def _flags(line: str) -> list[str]:
        flags = []
        for word in line.split()[1:]:
            if not word.startswith("--"):
                break
            flags.append(word)
        return flags

    def _mount_violation(self, word: str, stages: set[str]) -> str | None:
        name, sep, value = word.partition("=")
        if name != "--mount" or not sep:
            return _UNSUPPORTED
        for field in value.split(","):
            key, has_value, ref = field.partition("=")
            if key == "from" and has_value and _MOUNT_VALUE.fullmatch(ref):
                if not self._allowed(ref, stages):
                    return "from-not-allowed"
            elif key in _MOUNT_BARE and not has_value:
                continue
            elif key not in _MOUNT_KEYS or not _MOUNT_VALUE.fullmatch(ref):
                return _UNSUPPORTED
            elif key == "type" and ref not in _MOUNT_TYPES:
                return _UNSUPPORTED
        return None

    @staticmethod
    def _logical_lines(text: str) -> list[tuple[int, str]]:
        """Join backslash continuations; number is the first physical line."""
        out, buffer, start = [], "", 0
        for number, raw in enumerate(text.splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                if not buffer and stripped:
                    out.append((number, stripped))
                continue
            start = start or number
            buffer += stripped.removesuffix("\\")
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
