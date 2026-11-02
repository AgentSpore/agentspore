"""Mandatory vulnerability scan of verified image content before registry publication."""

import asyncio
import contextlib
import json
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from app.services.deployment.builder import Builder, BuildRequest, BuildResult
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.image_scan_artifact import VerifiedScanInput
from app.services.deployment.process import run_tool

T = TypeVar("T")
Runner = Callable[..., Awaitable[tuple[int, bytes]]]


@dataclass
class TrivyImageScanner:
    """Refresh private databases, freeze policy, then scan offline with bounded database age."""

    out_dir: Path
    run: Runner = run_tool
    timeout: float = 300.0
    MAX_DB_BYTES = 4 * 1024**3
    DB_FILES = (("db", "trivy.db", 2, 24), ("java-db", "trivy-java.db", 1, 72))

    async def scan(self, result: BuildResult) -> None:
        """Reject vulnerable, unsupported or unscannable images with fixed safe details."""
        try:
            with tempfile.TemporaryDirectory(prefix="a4-scan-", dir=self.out_dir) as tmp:
                workspace = Path(tmp)
                staging = workspace / "staging"
                staging.mkdir(mode=0o700)
                environment = {"TMPDIR": str(staging)}
                source = await self._joined(
                    lambda: VerifiedScanInput.prepare(result, self.out_dir, workspace)
                )
                config, ignore = workspace / "policy.yaml", workspace / "ignore"
                config.write_text("{}\n")
                ignore.write_text("")
                cache = workspace / "cache"
                base = ["trivy", "--cache-dir", str(cache), "--config", str(config), "image"]
                for flag in ("--download-db-only", "--download-java-db-only"):
                    rc, _ = await self.run(
                        [*base, flag, "--no-progress", "--quiet", "--disable-telemetry"],
                        cwd=workspace,
                        timeout=self.timeout,
                        env=environment,
                    )
                    if rc:
                        raise DeployRejectedError("tool_failed", "image database refresh failed")
                self._validate_databases(cache)
                argv = self._scan_argv(base, source, ignore)
                rc, report = await self.run(
                    argv, cwd=workspace, timeout=self.timeout, env=environment
                )
                self._validate_databases(cache)
                await self._joined(source.verify)
                self._verdict(rc, report, source)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DeployRejectedError(
                "tool_failed", "image scan input or database invalid"
            ) from exc

    @staticmethod
    def _scan_argv(base: list[str], source: VerifiedScanInput, ignore: Path) -> list[str]:
        return [
            *base,
            "--input",
            f"{source.layout}@{source.digest}",
            "--parallel",
            "1",
            "--scanners",
            "vuln",
            "--pkg-types",
            "os,library",
            "--severity",
            "CRITICAL",
            "--exit-code",
            "42",
            "--format",
            "json",
            "--list-all-pkgs",
            "--ignorefile",
            str(ignore),
            "--ignore-unfixed=false",
            "--cache-backend",
            "memory",
            "--offline-scan",
            "--skip-db-update",
            "--skip-java-db-update",
            "--skip-check-update",
            "--skip-version-check",
            "--skip-vex-repo-update",
            "--disable-telemetry",
            "--no-progress",
            "--quiet",
        ]

    @staticmethod
    async def _joined(operation: Callable[[], T]) -> T:
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # INVARIANT(#170): cleanup must not race a thread still writing the private layout.
            while not task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait({task})
            task.exception()
            raise

    @classmethod
    def _validate_databases(cls, cache: Path) -> None:
        now = datetime.now(timezone.utc)
        total = 0
        for directory, filename, version, max_age in cls.DB_FILES:
            root = cache / directory
            database = root / filename
            metadata = root / "metadata.json"
            if database.is_symlink() or not database.is_file() or metadata.is_symlink():
                raise ValueError("database invalid")
            size = database.stat().st_size
            total += size
            if not size or total > cls.MAX_DB_BYTES or metadata.stat().st_size > 16384:
                raise ValueError("database size invalid")
            data = json.loads(metadata.read_bytes())
            if not isinstance(data, dict) or not all(
                isinstance(data.get(key), str) for key in ("UpdatedAt", "NextUpdate")
            ):
                raise ValueError("database metadata invalid")
            updated = datetime.fromisoformat(data["UpdatedAt"].replace("Z", "+00:00"))
            next_update = datetime.fromisoformat(data["NextUpdate"].replace("Z", "+00:00"))
            if (
                type(data["Version"]) is not int
                or data["Version"] != version
                or updated.tzinfo is None
                or next_update.tzinfo is None
                or not timedelta(0) <= now - updated <= timedelta(hours=max_age)
                or next_update <= updated
                or now >= next_update
            ):
                raise ValueError("database metadata invalid")

    @classmethod
    def _verdict(cls, rc: int, raw: bytes, source: VerifiedScanInput) -> None:
        data = json.loads(raw)
        if (
            rc not in (0, 42)
            or not isinstance(data, dict)
            or data.get("SchemaVersion") != 2
            or data.get("ArtifactType") != "container_image"
        ):
            raise ValueError("report invalid")
        metadata = data.get("Metadata")
        results = data.get("Results", [])
        if not isinstance(metadata, dict) or not isinstance(results, list):
            raise ValueError("report coverage absent")
        source.verify_report_identity(metadata)
        os_info = metadata.get("OS")
        if os_info is not None and (
            not isinstance(os_info, dict) or not os_info.get("Family") or not results
        ):
            raise ValueError("image operating system unsupported")
        critical = False
        for result in results:
            critical = cls._critical_result(result) or critical
        if critical != (rc == 42):
            raise ValueError("scanner exit code inconsistent")
        if critical:
            raise DeployRejectedError("image_vulnerable", "critical image vulnerability found")

    @staticmethod
    def _critical_result(result: object) -> bool:
        if not isinstance(result, dict) or result.get("Class") not in ("os-pkgs", "lang-pkgs"):
            raise ValueError("result coverage invalid")
        packages = result.get("Packages")
        if not result.get("Type") or not isinstance(packages, list) or not packages:
            raise ValueError("package coverage absent")
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise ValueError("vulnerability report invalid")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict) or vulnerability.get("Severity") != "CRITICAL":
                raise ValueError("vulnerability severity invalid")
        return bool(vulnerabilities)


@dataclass
class ImageScannedBuilder:
    """Do not return an image to the pusher until the mandatory image gate succeeds."""

    inner: Builder
    scanner: TrivyImageScanner

    async def build(self, request: BuildRequest) -> BuildResult:
        """Delete a refused artifact even though the outer pusher never received it."""
        result = await self.inner.build(request)
        try:
            await self.scanner.scan(result)
        except BaseException:
            if (
                result.artifact is not None
                and result.artifact.parent.resolve() == self.scanner.out_dir.resolve()
            ):
                with contextlib.suppress(OSError):
                    result.artifact.unlink(missing_ok=True)
            raise
        return result
