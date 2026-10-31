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
from typing import BinaryIO

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
_NAME_OK = re.compile(
    r"index\.json|oci-layout|blobs/?|blobs/sha256/?|blobs/sha256/[0-9a-f]{64}"
)  # the OCI image layout; anything else (NUL, `..`, aliases) is not the exporter's output
_PAX_KEYS = (b"path", b"linkpath", b"mtime", b"atime", b"ctime")


def _refuse(detail: str) -> DeployRejectedError:
    return DeployRejectedError("build_failed", detail)


def _checksum_ok(block: bytes) -> bool:
    """tarfile's rule: the stored sum matches the unsigned or the signed byte sum."""
    try:
        stored = int(block[148:156].strip(b"\0 "), 8)
    except ValueError:
        return False
    rest = block[:148] + b" " * 8 + block[156:]
    return stored in (sum(rest), sum(rest) - 256 * sum(byte >= 128 for byte in rest))


def _check_pax_records(payload: bytes) -> None:
    """Only `<len> <key>=<value>\\n` records for path/time keys: no `size` or other overrides."""
    pos = 0
    while pos < len(payload):
        space = payload.find(b" ", pos)
        digits = payload[pos:space]
        if space < 0 or not digits.isdigit() or not 0 < int(digits) <= len(payload) - pos:
            raise _refuse("image tar has a malformed extended header")
        record = payload[space + 1 : pos + int(digits)]
        key, eq, value = record.partition(b"=")
        if not eq or not record.endswith(b"\n") or key not in _PAX_KEYS:
            raise _refuse("image tar extended header carries an unsupported key")
        if key == b"path" and not _NAME_OK.fullmatch(value[:-1].decode("utf-8", "replace")):
            raise _refuse("image tar extended header names a path outside the OCI layout")
        pos += int(digits)


def _skip_data(raw: BinaryIO, kind: bytes, size: int) -> None:
    if kind in _PAX_TYPES:
        if size > _MAX_PAX_BYTES:
            raise _refuse("image tar has an oversized extended header")
        _check_pax_records(raw.read(size))
        raw.seek(-size % _BLOCK, 1)
    else:
        raw.seek(-(-size // _BLOCK) * _BLOCK, 1)


def _screen_headers(tar: Path) -> int:
    """Walk the raw headers before `tarfile` does (it buffers pax/GNU payloads without a cap).

    Returns the offset where the archive ends; `tarfile` must stop at the same place.
    """
    with tar.open("rb") as raw:
        for _ in range(_MAX_MEMBERS + 1):
            start = raw.tell()
            block = raw.read(_BLOCK)
            if len(block) < _BLOCK or block == b"\0" * _BLOCK:
                return start
            kind = block[156:157]
            try:
                size = int(block[124:136].strip(b"\0 ") or b"0", 8)
            except ValueError as exc:
                raise _refuse("image tar header is malformed") from exc
            if kind not in _PLAIN_MEMBERS + _PAX_TYPES or not _checksum_ok(block):
                raise _refuse("image tar has an unexpected member")
            _skip_data(raw, kind, size)
    raise _refuse("image tar has too many members")


def _check_member(member: tarfile.TarInfo, seen: set[str]) -> None:
    """Plain type, an OCI-layout name, no repeats; `seen` collects earlier member names."""
    name = member.name.removesuffix("/") if member.isdir() else member.name
    if member.type not in _PLAIN_MEMBERS or not _NAME_OK.fullmatch(member.name) or name in seen:
        raise _refuse("image tar has an unexpected member")
    seen.add(name)


@dataclass(frozen=True)
class LayoutScan:
    """`index.json` bytes plus `blobs/sha256/<hex>` members as digest -> (data offset, size)."""

    index: bytes
    blobs: dict[str, tuple[int, int]]


def scan_layout(tar: Path) -> LayoutScan:
    """Screen the whole tar; nothing is extracted, any odd member refuses it."""
    found: bytes | None = None
    blobs: dict[str, tuple[int, int]] = {}
    seen: set[str] = set()
    try:
        end = _screen_headers(tar)
        with tarfile.open(tar, "r:") as tf:
            for member in tf:
                _check_member(member, seen)
                if member.name.startswith("blobs/sha256/") and member.isreg():
                    blobs["sha256:" + member.name.rpartition("/")[2]] = (
                        member.offset_data,
                        member.size,
                    )
                if member.name != _INDEX:
                    continue
                if member.size > _MAX_INDEX_BYTES or not member.isreg():
                    raise _refuse("image tar index.json is invalid")
                stream = tf.extractfile(member)
                found = stream.read(_MAX_INDEX_BYTES + 1) if stream else None
            if tf.offset != end:
                raise _refuse("image tar parses differently than its headers say")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise _refuse("image tar is unreadable") from exc
    if found is None:
        raise _refuse("image tar has no index.json")
    return LayoutScan(found, blobs)


def _read_index(tar: Path) -> bytes:
    return scan_layout(tar).index


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
