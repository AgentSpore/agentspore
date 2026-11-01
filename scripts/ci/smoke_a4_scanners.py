"""Run inside the final image with PYTHONPATH=/app; optional online dependency check."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from app.services.deployment.errors import DeployRejectedError
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
