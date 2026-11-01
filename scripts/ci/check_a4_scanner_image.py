"""Accept the exact native backend build with bounded, isolated real scanner probes."""

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def image_from_receipt(path: Path) -> str:
    """Validate build identity and daemon architecture before starting containers."""
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise ValueError("scanner_image_receipt")
    image = record.get("image_id", "")
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("scanner_image_identity")
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], timeout=15, text=True
    ).strip()
    if (
        record.get("service"),
        record.get("architecture"),
        record.get("source_sha"),
    ) != (
        "backend",
        "amd64",
        head,
    ):
        raise ValueError("scanner_image_receipt")
    inspected = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}} {{.Os}} {{.Architecture}}",
            image,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    ).stdout.strip()
    if inspected != f"{image} linux amd64":
        raise ValueError("scanner_image_architecture")
    return image


def probe(image: str, script: bytes, online: bool) -> dict:
    """Run only the owned container and clean it even on timeout or interrupted client."""
    name = "a4-scanner-smoke-" + uuid4().hex
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "-i",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,mode=1777",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        "bridge" if online else "none",
        "--entrypoint",
        "/app/.venv/bin/python",
        image,
        "-",
    ]
    if online:
        command.append("--online")
    try:
        result = subprocess.run(
            command, input=script, capture_output=True, timeout=330, check=True
        )
        report = json.loads(result.stdout)
        expected = {"offline": "passed", "online": "passed" if online else "not_run"}
        if report != expected:
            raise ValueError("scanner_smoke_report")
        return expected
    finally:
        # --rm may have already removed it; verify absence after the targeted cleanup.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name=^/{name}$"],
            capture_output=True,
            timeout=15,
            check=True,
        )
        if remaining.stdout.strip():
            raise ValueError("scanner_smoke_cleanup")


def interrupted(signum, frame) -> None:
    """Unwind the active subprocess and its owned container on runner termination."""
    raise InterruptedError("scanner_smoke_interrupted")


def main() -> int:
    """Write only a closed receipt; raw scanner or Docker output never reaches CI logs."""
    reports = Path(os.environ["RUNNER_TEMP"]) / "release-image-reports"
    output = reports / "a4-scanner-smoke.json"
    receipt = {"status": "failed"}
    try:
        image = image_from_receipt(reports / "backend.json")
        receipt["image_id"] = image
        script = (Path(__file__).parent / "smoke_a4_scanners.py").read_bytes()
        receipt["offline"] = probe(image, script, False)
        receipt["online"] = probe(image, script, True)
        receipt["status"] = "passed"
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        pass
    output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    print(json.dumps({"scanner_smoke": receipt["status"]}))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    sys.exit(main())
