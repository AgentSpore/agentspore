"""Trusted-side check that the OCI tarball holds the image the untrusted daemon reported.

`BuildKitBuilder` takes its digest from buildkitd's metadata. Before anything pins by that
digest, the worker reads the tar's own `index.json` and refuses on any disagreement.
"""

import contextlib
import json
import posixpath
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from app.services.deployment.builder import Builder, BuildRequest, BuildResult
from app.services.deployment.errors import DeployRejectedError

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_INDEX = "index.json"
_MAX_INDEX_BYTES = 64 * 1024
_MAX_MEMBERS = 10_000
_PLAIN_MEMBERS = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
_PAX_TYPES = (tarfile.XHDTYPE, tarfile.XGLTYPE)
_BLOCK = tarfile.BLOCKSIZE
_MAX_PAX_BYTES = 64 * 1024


def _refuse(detail: str) -> DeployRejectedError:
    return DeployRejectedError("build_failed", detail)


def _screen_headers(tar: Path) -> None:
    """Walk the raw headers before `tarfile` does: it buffers pax/GNU payloads without a cap."""
    with tar.open("rb") as raw:
        for _ in range(_MAX_MEMBERS + 1):
            block = raw.read(_BLOCK)
            if len(block) < _BLOCK or block == b"\0" * _BLOCK:
                return
            kind = block[156:157]
            try:
                size = int(block[124:136].strip(b"\0 ") or b"0", 8)
            except ValueError as exc:
                raise _refuse("image tar header is malformed") from exc
            if kind not in _PLAIN_MEMBERS + _PAX_TYPES:
                raise _refuse("image tar has an unexpected member")
            if kind in _PAX_TYPES and size > _MAX_PAX_BYTES:
                raise _refuse("image tar has an oversized extended header")
            raw.seek(-(-size // _BLOCK) * _BLOCK, 1)
    raise _refuse("image tar has too many members")


def _read_index(tar: Path) -> bytes:
    """Return `index.json` bytes; nothing is extracted, any odd member refuses the tar."""
    found: bytes | None = None
    seen: set[str] = set()
    try:
        _screen_headers(tar)
        with tarfile.open(tar, "r:") as tf:
            for member in tf:
                name = member.name.removesuffix("/") if member.isdir() else member.name
                if (
                    name.startswith("/")
                    or ".." in name.split("/")
                    or posixpath.normpath(name) != name
                    or name in seen
                ):
                    raise _refuse("image tar has an unexpected member")
                seen.add(name)
                if member.name != _INDEX:
                    continue
                if member.size > _MAX_INDEX_BYTES or not member.isreg():
                    raise _refuse("image tar index.json is invalid")
                stream = tf.extractfile(member)
                found = stream.read(_MAX_INDEX_BYTES + 1) if stream else None
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise _refuse("image tar is unreadable") from exc
    if found is None:
        raise _refuse("image tar has no index.json")
    return found


def verify_oci_digest(tar: Path, digest: str) -> None:
    """Refuse with `build_failed` unless `index.json` names exactly one manifest, `digest`."""
    try:
        manifests = json.loads(_read_index(tar))["manifests"]
        digests = [m["digest"] for m in manifests]
    except (ValueError, KeyError, TypeError) as exc:
        raise _refuse("image tar index.json is malformed") from exc
    if len(digests) != 1 or not DIGEST_RE.fullmatch(str(digests[0])):
        raise _refuse("image tar must hold exactly one manifest")
    if digests[0] != digest:
        raise _refuse("image digest does not match the build metadata")


@dataclass
class DigestBoundBuilder:
    """Checks `artifact` against the digest in `image_ref`; a refusal deletes the tarball."""

    inner: Builder

    async def build(self, request: BuildRequest) -> BuildResult:
        result = await self.inner.build(request)
        tar = result.artifact
        if tar is None:
            raise _refuse("builder reported no image tarball")
        try:
            verify_oci_digest(tar, result.image_ref.rpartition("@")[2])
        except BaseException:
            with contextlib.suppress(OSError):
                tar.unlink(missing_ok=True)
            raise
        return result
