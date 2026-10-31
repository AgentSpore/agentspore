"""Builder over a remote rootless BuildKit daemon, driven through `buildctl`.

A `RUN` step can ptrace buildkitd (`--oci-worker-no-process-sandbox`), so the daemon is given
only the build context: no secrets, SSH agent, registry auth or entitlements. The result is an
OCI tarball on the trusted side; the worker scans and pushes it afterwards.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.services.deployment.builder import Builder, BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.oci import DigestBoundBuilder
from app.services.deployment.process import run_tool

_DOCKERFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_UNSAFE_TAG_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_URL_CREDENTIALS = re.compile(r"://[^/\s@]+@")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_TAG_LENGTH = 128
_TAIL_CHARS = 300
_MAX_METADATA_BYTES = 64 * 1024
_PROXY_ARGS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


@dataclass
class BuildKitBuilder:
    """`Builder` that runs `buildctl build` against `addr` and writes `<out_dir>/<tag>.tar`.

    `proxy_url` is an IP: `RUN` steps under runsc have no Docker DNS. `out_dir` must sit on a
    size-capped volume; the 1 GiB image cap is checked after the build, not while it writes.
    The digest comes from the untrusted daemon's metadata; wrap with `DigestBoundBuilder` (see
    `builder_from_settings`) to check it against the tar's `index.json`.
    """

    addr: str
    tls_dir: Path
    out_dir: Path
    proxy_url: str = "http://10.213.77.11:3128"
    timeout: float = 600.0
    max_image_bytes: int = 1024**3
    buildctl: str = "buildctl"

    async def build(self, request: BuildRequest) -> BuildResult:
        """Build `request`; refuse with `manifest_invalid`, `build_failed` or `image_too_large`."""
        self._check_dockerfile(request.dockerfile)
        stem = self._tag_to_filename(request.tag)
        tar = self.out_dir / f"{stem}.tar"
        meta = self.out_dir / f"{stem}.json"
        try:
            rc, stdout = await run_tool(
                self._argv(request, tar, meta), cwd=self.out_dir, timeout=self.timeout
            )
            if rc != 0:
                raise DeployRejectedError("build_failed", self._failure_detail(rc, stdout))
            digest = self._read_digest(meta)
            size = self._tar_size(tar)
            if size > self.max_image_bytes:
                raise DeployRejectedError(
                    "image_too_large", f"{size} bytes over {self.max_image_bytes}"
                )
        except BaseException:
            tar.unlink(missing_ok=True)
            raise
        finally:
            meta.unlink(missing_ok=True)
        return BuildResult(image_ref=f"{tar}@{digest}", image_bytes=size)

    def _argv(self, request: BuildRequest, tar: Path, meta: Path) -> list[str]:
        ctx = str(request.context_dir)
        argv = [
            self.buildctl,
            "--addr", self.addr,
            "--tlscacert", str(self.tls_dir / "ca.pem"),
            "--tlscert", str(self.tls_dir / "client.pem"),
            "--tlskey", str(self.tls_dir / "client-key.pem"),
            "build",
            "--frontend", "dockerfile.v0",
            "--local", f"context={ctx}",
            "--local", f"dockerfile={ctx}",
            "--opt", f"filename={request.dockerfile}",
        ]  # fmt: skip
        for name in _PROXY_ARGS:
            argv += ["--opt", f"build-arg:{name}={self.proxy_url}"]
        return [*argv, "--output", f"type=oci,dest={tar}", "--metadata-file", str(meta)]

    @staticmethod
    def _check_dockerfile(name: str) -> None:
        if not _DOCKERFILE_NAME.fullmatch(name) or ".." in name:
            raise DeployRejectedError("manifest_invalid", "dockerfile must be a plain file name")

    @staticmethod
    def _tag_to_filename(tag: str) -> str:
        stem = _UNSAFE_TAG_CHARS.sub("_", tag)
        if not stem or stem.startswith(".") or len(stem) > _MAX_TAG_LENGTH:
            raise DeployRejectedError("manifest_invalid", "tag cannot be used as a file name")
        return stem

    @staticmethod
    def _failure_detail(rc: int, stdout: bytes) -> str:
        # buildctl writes errors to stderr, which run_tool discards: usually only `rc=N` remains.
        redacted = _URL_CREDENTIALS.sub("://***@", stdout.decode(errors="replace"))
        return f"buildctl rc={rc}: {redacted[-_TAIL_CHARS:].strip()}".rstrip(": ")

    @staticmethod
    def _read_digest(meta: Path) -> str:
        try:
            if meta.stat().st_size > _MAX_METADATA_BYTES:
                raise DeployRejectedError("build_failed", "buildctl metadata is too large")
            digest = json.loads(meta.read_text())["containerimage.digest"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DeployRejectedError("build_failed", "buildctl metadata has no digest") from exc
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise DeployRejectedError("build_failed", "buildctl digest is malformed")
        return digest

    @staticmethod
    def _tar_size(tar: Path) -> int:
        try:
            return tar.stat().st_size
        except OSError as exc:
            raise DeployRejectedError("build_failed", "buildctl produced no image") from exc


def builder_from_settings(settings: Settings) -> Builder:
    """`FakeBuilder` by default; with `A4_BUILDER=buildkit`, a digest-bound `BuildKitBuilder`."""
    if settings.a4_builder == "fake":
        return FakeBuilder()
    return DigestBoundBuilder(
        BuildKitBuilder(
            addr=settings.a4_buildkit_addr,
            tls_dir=settings.a4_buildkit_tls_dir,
            out_dir=settings.a4_builder_out_dir,
            proxy_url=settings.a4_builder_proxy_url,
        )
    )
