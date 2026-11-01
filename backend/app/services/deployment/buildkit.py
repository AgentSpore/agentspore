"""Builder over a remote rootless BuildKit daemon, driven through `buildctl`.

A `RUN` step can ptrace buildkitd (`--oci-worker-no-process-sandbox`), so the daemon is given
only the build context: no secrets, SSH agent, registry auth or entitlements. The result is an
OCI tarball on the trusted side; the worker scans and pushes it afterwards.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import Settings
from app.services.deployment.builder import Builder, BuildRequest, BuildResult, FakeBuilder
from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.oci import DIGEST_RE, DigestBoundBuilder
from app.services.deployment.process import ToolOutput, redact, run_tool_captured
from app.services.deployment.recycle import DaemonRecycler, RecyclingBuilder
from app.services.deployment.registry_push import PushingBuilder, RegistryPusher

_DOCKERFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_UNSAFE_TAG_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_TAG_LENGTH = 128
_TLS_FILES = ("ca.pem", "client.pem", "client-key.pem")
_TAIL_CHARS = 300
_STDOUT_TAIL = 4096
_MAX_METADATA_BYTES = 64 * 1024
# Cargo is absent on purpose: it cannot replace a source from env (doc.rust-lang.org/cargo/
# reference/config.html, `source.<name>.replace-with`: "Environment: not supported"); the runbook
# gives authors a `.cargo/config.toml` instead.
_MIRROR_ARGS = (
    "PIP_INDEX_URL=http://{h}:8081/simple/",
    "PIP_TRUSTED_HOST={h}",
    "npm_config_registry=http://{h}:8082/",
    "npm_config_audit=false",
    "GOPROXY=http://{h}:8083",
    "GOSUMDB=sum.golang.org http://{h}:8083/sumdb",
)


@dataclass
class BuildKitBuilder:
    """`Builder` that runs `buildctl build` against `addr` and writes `<out_dir>/<tag>.tar`.

    `mirror_host` is the package mirrors' IPv4 literal (`Settings.a4_pkg_mirror_host`): `RUN`
    steps under runsc have no Docker DNS. `out_dir` must sit on a size-capped
    volume; the 1 GiB image cap is checked after the build, not while it writes.
    The digest comes from the untrusted daemon's metadata; wrap with `DigestBoundBuilder` (see
    `builder_from_settings`) to check it against the tar's `index.json`.
    """

    addr: str
    tls_dir: Path
    out_dir: Path
    mirror_host: str = "10.213.77.12"
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
            out = await run_tool_captured(
                self._argv(request, tar, meta), cwd=self.out_dir, timeout=self.timeout
            )
            if out.rc != 0:
                raise DeployRejectedError("build_failed", self._failure_detail(out))
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
        return BuildResult(image_ref=f"{tar}@{digest}", image_bytes=size, artifact=tar)

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
        for template in _MIRROR_ARGS:
            argv += ["--opt", f"build-arg:{template.format(h=self.mirror_host)}"]
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
    def _failure_detail(out: ToolOutput) -> str:
        # buildctl reports errors on stderr (redacted on capture); stdout arrives raw, so only
        # its bounded tail, from a line start, is redacted here.
        tail = out.stdout[-_STDOUT_TAIL:]
        if len(out.stdout) > _STDOUT_TAIL:
            tail = tail.partition(b"\n")[2]
        text = f"{out.stderr.decode(errors='replace')}\n{redact(tail).decode(errors='replace')}"
        return f"buildctl rc={out.rc}: {text.strip()[-_TAIL_CHARS:]}".rstrip(": ")

    @staticmethod
    def _read_digest(meta: Path) -> str:
        try:
            if meta.stat().st_size > _MAX_METADATA_BYTES:
                raise DeployRejectedError("build_failed", "buildctl metadata is too large")
            digest = json.loads(meta.read_text())["containerimage.digest"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DeployRejectedError("build_failed", "buildctl metadata has no digest") from exc
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise DeployRejectedError("build_failed", "buildctl digest is malformed")
        return digest

    @staticmethod
    def _tar_size(tar: Path) -> int:
        try:
            return tar.stat().st_size
        except OSError as exc:
            raise DeployRejectedError("build_failed", "buildctl produced no image") from exc


def validate_builder_settings(settings: Settings) -> None:
    """Refuse a misconfigured `A4_BUILDER=buildkit` at worker start; names only, never contents."""
    if settings.a4_builder != "buildkit":
        return
    addr = urlsplit(settings.a4_buildkit_addr)
    try:
        port = addr.port
    except ValueError:  # non-numeric or out of range
        port = None
    if addr.scheme != "tcp" or not addr.hostname or port is None:
        raise RuntimeError("A4_BUILDKIT_ADDR must look like tcp://host:port")
    for name in _TLS_FILES:
        path = settings.a4_buildkit_tls_dir / name
        if not os.access(path, os.R_OK) or not path.is_file():
            raise RuntimeError(f"A4_BUILDKIT_TLS_DIR: {name} is missing or unreadable")
    registry = urlsplit(settings.a4_registry_url)
    if registry.scheme != "http" or not registry.hostname or registry.username or registry.query:
        raise RuntimeError("A4_REGISTRY_URL must look like http://host:port")
    out_dir = settings.a4_builder_out_dir
    if not out_dir.is_dir() or not os.access(out_dir, os.W_OK):
        raise RuntimeError("A4_BUILDER_OUT_DIR must be an existing writable directory")


def builder_from_settings(settings: Settings) -> Builder:
    """`FakeBuilder` by default; with `A4_BUILDER=buildkit`, a validated, digest-bound
    `BuildKitBuilder` that restarts its daemon after every build through the restart gate,
    then pushes the image to the private registry and returns a digest-only reference."""
    if settings.a4_builder == "fake":
        return FakeBuilder()
    validate_builder_settings(settings)
    inner = DigestBoundBuilder(
        BuildKitBuilder(
            addr=settings.a4_buildkit_addr,
            tls_dir=settings.a4_buildkit_tls_dir,
            out_dir=settings.a4_builder_out_dir,
            mirror_host=settings.a4_pkg_mirror_host,
        )
    )
    recycler = DaemonRecycler(
        gate_url=settings.a4_recycle_gate_url,
        container=settings.a4_buildkit_container,
        api_version=settings.a4_recycle_api_version,
    )
    pusher = RegistryPusher(settings.a4_registry_url, settings.a4_registry_ref_host)
    return PushingBuilder(RecyclingBuilder(inner, recycler), pusher)
