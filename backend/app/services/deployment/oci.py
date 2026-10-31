"""Trusted-side check that the OCI tarball holds the image the untrusted daemon reported.

`BuildKitBuilder` takes its digest from buildkitd's metadata. Before anything pins by that
digest, the worker reads the tar's own `index.json` and refuses on any disagreement.
"""

import contextlib
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from app.services.deployment.builder import Builder, BuildRequest, BuildResult
from app.services.deployment.errors import DeployRejectedError

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_INDEX = "index.json"
_MAX_INDEX_BYTES = 64 * 1024
_MAX_MEMBERS = 10_000
_PLAIN_MEMBERS = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)


def _refuse(detail: str) -> DeployRejectedError:
    return DeployRejectedError("build_failed", detail)


def _read_index(tar: Path) -> bytes:
    """Return `index.json` bytes; nothing is extracted, any odd member refuses the tar."""
    found: bytes | None = None
    try:
        with tarfile.open(tar, "r:") as tf:
            for count, member in enumerate(tf, 1):
                if count > _MAX_MEMBERS:
                    raise _refuse("image tar has too many members")
                parts = member.name.split("/")
                if (
                    member.type not in _PLAIN_MEMBERS
                    or member.name.startswith("/")
                    or ".." in parts
                ):
                    raise _refuse("image tar has an unexpected member")
                if member.name != _INDEX:
                    continue
                if found is not None or member.size > _MAX_INDEX_BYTES or not member.isreg():
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
    if len(digests) != 1 or not _DIGEST.fullmatch(str(digests[0])):
        raise _refuse("image tar must hold exactly one manifest")
    if digests[0] != digest:
        raise _refuse("image digest does not match the build metadata")


@dataclass
class DigestBoundBuilder:
    """Wraps a builder whose `image_ref` is `<tar path>@<digest>`; a mismatch deletes the tar."""

    inner: Builder

    async def build(self, request: BuildRequest) -> BuildResult:
        result = await self.inner.build(request)
        path, _, digest = result.image_ref.rpartition("@")
        tar = Path(path)
        try:
            verify_oci_digest(tar, digest)
        except BaseException:
            with contextlib.suppress(OSError):
                tar.unlink(missing_ok=True)
            raise
        return result
