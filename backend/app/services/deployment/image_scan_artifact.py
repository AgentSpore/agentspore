"""Materialize a verified OCI graph for the trusted image scanner."""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.services.deployment.builder import BuildResult
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.image_plan import plan_image


@dataclass
class VerifiedScanInput:
    """Bind one private scan layout to the original immutable build artifact."""

    artifact: Path
    fingerprint: tuple[int, int, int, str]
    layout: Path
    digest: str
    files: dict[str, str]
    MAX_BYTES = 1024**3

    @classmethod
    def prepare(cls, result: BuildResult, out_dir: Path, workspace: Path) -> "VerifiedScanInput":
        """Copy only verified content-addressed blobs; never extract archive paths or layers."""
        tar = result.artifact
        if tar is None or tar.parent.resolve() != out_dir.resolve():
            raise DeployRejectedError("tool_failed", "image artifact location invalid")
        before = cls.fingerprint_of(tar)
        plan = plan_image(tar)
        if plan.manifest.digest != result.image_ref.rpartition("@")[2]:
            raise DeployRejectedError("build_failed", "image manifest binding failed")
        layout = workspace / "layout"
        (layout / "blobs/sha256").mkdir(parents=True)
        files = {}
        with tar.open("rb") as source:
            for blob in (plan.manifest, *plan.blobs):
                name = "blobs/sha256/" + blob.digest[7:]
                destination = layout / name
                source.seek(blob.offset)
                remaining = blob.size
                with destination.open("xb") as target:
                    while remaining:
                        chunk = source.read(min(1024**2, remaining))
                        if not chunk:
                            raise DeployRejectedError("build_failed", "image artifact truncated")
                        target.write(chunk)
                        remaining -= len(chunk)
                files[name] = blob.digest[7:]
        documents = {
            "oci-layout": {"imageLayoutVersion": "1.0.0"},
            "index.json": {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": plan.media_type,
                        "digest": plan.manifest.digest,
                        "size": plan.manifest.size,
                    }
                ],
            },
        }
        for name, document in documents.items():
            data = json.dumps(document).encode()
            (layout / name).write_bytes(data)
            files[name] = hashlib.sha256(data).hexdigest()
        value = cls(tar, before, layout, plan.manifest.digest, files)
        value.verify()
        for name in files:
            (layout / name).chmod(0o444)
        return value

    @staticmethod
    def fingerprint_of(path: Path) -> tuple[int, int, int, str]:
        """Hash a bounded regular file without following a replacement symlink."""
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > VerifiedScanInput.MAX_BYTES:
                raise DeployRejectedError("tool_failed", "image artifact size or type invalid")
            digest = hashlib.sha256()
            count = 0
            while chunk := source.read(1024**2):
                count += len(chunk)
                if count > VerifiedScanInput.MAX_BYTES:
                    raise DeployRejectedError("tool_failed", "image artifact size invalid")
                digest.update(chunk)
            if count != info.st_size:
                raise DeployRejectedError("tool_failed", "image artifact changed")
            return info.st_dev, info.st_ino, info.st_size, digest.hexdigest()

    def verify(self) -> None:
        """Refuse changed original bytes or scan-layout content before publication."""
        if self.fingerprint_of(self.artifact) != self.fingerprint:
            raise DeployRejectedError("tool_failed", "image artifact changed")
        for name, digest in self.files.items():
            if self.fingerprint_of(self.layout / name)[3] != digest:
                raise DeployRejectedError("tool_failed", "image scan input changed")

    def verify_report_identity(self, metadata: dict) -> None:
        """Require the report to describe this exact verified configuration and rootfs."""
        manifest = json.loads((self.layout / "blobs/sha256" / self.digest[7:]).read_bytes())
        digest = manifest["config"]["digest"]
        path = self.layout / "blobs/sha256" / digest[7:]
        if path.stat().st_size > 1024**2:
            raise ValueError("image config too large")
        config = json.loads(path.read_bytes())
        if not isinstance(config, dict):
            raise ValueError("image config invalid")
        observed = metadata.get("ImageConfig")
        rootfs = config.get("rootfs")
        if (
            metadata.get("ImageID") != digest
            or not isinstance(observed, dict)
            or config.get("os") != "linux"
            or not config.get("architecture")
            or not isinstance(rootfs, dict)
            or rootfs.get("type") != "layers"
            or not isinstance(rootfs.get("diff_ids"), list)
            or metadata.get("DiffIDs") != rootfs["diff_ids"]
            or any(observed.get(key) != config.get(key) for key in ("os", "architecture", "rootfs"))
        ):
            raise ValueError("report image identity invalid")
