"""Run inside the final image with PYTHONPATH=/app; optional online dependency check."""

import argparse
import asyncio
import hashlib
import os
import json
import tempfile
from pathlib import Path

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.process import run_tool
from app.services.deployment.scans import GitleaksScanner, OsvScanner, run_prescan


async def rejected(root: Path, scanner, reason: str) -> None:
    """Require the real prescan path to reject with the expected stable code."""
    try:
        await run_prescan(root, [scanner])
    except DeployRejectedError as exc:
        if exc.reason_code != reason:
            raise RuntimeError("scanner_smoke_wrong_reason") from None
    else:
        raise RuntimeError("scanner_smoke_not_rejected")


async def smoke(online: bool) -> dict:
    """Exercise actual binaries on temporary nonsecret files, never a user repository."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        rc, version = await run_tool(["trivy", "--version"], cwd=root, timeout=15)
        if rc != 0 or version.splitlines()[0:1] != [b"Version: 0.76.1"]:
            raise RuntimeError("trivy_smoke_version")
        binary = Path("/usr/local/bin/trivy")
        license_file = Path("/usr/local/share/licenses/trivy/LICENSE")
        if binary.stat().st_mode & 0o777 != 0o755 or not os.access(binary, os.X_OK):
            raise RuntimeError("trivy_smoke_permissions")
        if hashlib.sha256(license_file.read_bytes()).hexdigest() != (
            "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
        ):
            raise RuntimeError("trivy_smoke_license")
        (root / "README.txt").write_text("Synthetic scanner acceptance fixture.\n")
        if await GitleaksScanner().scan(root) or await OsvScanner().scan(root):
            raise RuntimeError("scanner_smoke_clean_failed")
        # Never-issued synthetic token: tests the default GitHub detector, not real credentials.
        token = "ghp_" + "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0iL3oR6"
        (root / "fixture.txt").write_text(f"token={token} # gitleaks:allow\n")
        (root / ".gitleaks.toml").write_text('[allowlist]\npaths = [".*"]\n')
        await rejected(root, GitleaksScanner(), "secret_found")
        (root / "fixture.txt").unlink()
        if online:
            (root / "pom.xml").write_text(
                "<project><modelVersion>4.0.0</modelVersion><groupId>test</groupId>"
                "<artifactId>fixture</artifactId><version>1</version><dependencies>"
                "<dependency><groupId>org.apache.logging.log4j</groupId>"
                "<artifactId>log4j-core</artifactId><version>2.14.1</version>"
                "</dependency></dependencies></project>"
            )
            await rejected(root, OsvScanner(), "dependency_vulnerable")
    return {"offline": "passed", "online": "passed" if online else "not_run"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    try:
        receipt = asyncio.run(smoke(args.online))
    except (RuntimeError, DeployRejectedError):
        print(json.dumps({"status": "failed"}))
        raise SystemExit(1) from None
    print(json.dumps(receipt, sort_keys=True))
