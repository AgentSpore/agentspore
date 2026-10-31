"""Turn the verified OCI tarball into a push plan: one image manifest, its config and layers.

The plan is built only from what the tar proves (sizes and sha256 recomputed locally).
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.oci import DIGEST_RE, scan_layout

MAX_LAYERS = 128
MAX_JSON_BYTES = 1024 * 1024  # manifest and config
_CHUNK = 1024 * 1024
_MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
_CONFIG_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
_LAYER_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
    "application/vnd.docker.image.rootfs.diff.tar.gzip",
}


@dataclass(frozen=True)
class Blob:
    digest: str
    size: int
    offset: int  # start of the data inside the tar


@dataclass(frozen=True)
class ImagePlan:
    manifest: Blob
    media_type: str
    body: bytes
    blobs: tuple[Blob, ...]  # config first, then layers, each digest once


def _refuse(detail: str) -> DeployRejectedError:
    return DeployRejectedError("build_failed", detail)


def _descriptor(raw: object, allowed: set[str], what: str) -> tuple[str, int]:
    """(digest, size) of a descriptor of an allowed type; `urls` or any other type refuses."""
    if not isinstance(raw, dict) or "urls" in raw or raw.get("mediaType") not in allowed:
        raise _refuse(f"image {what} descriptor is not allowed")
    digest, size = raw.get("digest"), raw.get("size")
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise _refuse(f"image {what} digest is malformed")
    if type(size) is not int or size < 0:
        raise _refuse(f"image {what} size is malformed")
    return digest, size


def _locate(blobs: dict[str, tuple[int, int]], digest: str, size: int) -> Blob:
    found = blobs.get(digest)
    if found is None or found[1] != size:
        raise _refuse("image tar lacks a blob its manifest names")
    return Blob(digest, size, found[0])


def _read(tar: Path, blob: Blob) -> bytes:
    with tar.open("rb") as raw:
        raw.seek(blob.offset)
        return raw.read(blob.size)


def verify_blob(tar: Path, blob: Blob) -> None:
    """Recompute sha256 over exactly `blob.size` bytes; the descriptor is only a claim."""
    hasher = hashlib.sha256()
    left = blob.size
    with tar.open("rb") as raw:
        raw.seek(blob.offset)
        while left:
            chunk = raw.read(min(_CHUNK, left))
            if not chunk:
                raise _refuse("image tar blob is truncated")
            hasher.update(chunk)
            left -= len(chunk)
    if "sha256:" + hasher.hexdigest() != blob.digest:
        raise _refuse("image tar blob does not match its digest")


def _manifest_json(tar: Path, blob: Blob, media_type: str) -> dict:
    if blob.size > MAX_JSON_BYTES:
        raise _refuse("image manifest is too large")
    verify_blob(tar, blob)
    try:
        doc = json.loads(_read(tar, blob))
    except ValueError as exc:
        raise _refuse("image manifest is not JSON") from exc
    layers = doc.get("layers") if isinstance(doc, dict) else None
    if (
        not isinstance(layers, list)
        or doc.get("schemaVersion") != 2
        or doc.get("mediaType", media_type) != media_type
    ):
        raise _refuse("image manifest is malformed")
    if not layers or len(layers) > MAX_LAYERS:
        raise _refuse("image manifest has too many or no layers")
    return doc


def plan_image(tar: Path) -> ImagePlan:
    """Exactly one plain image manifest; every blob recomputed against its digest and size."""
    scan = scan_layout(tar)
    try:
        entries = json.loads(scan.index)["manifests"]
    except (ValueError, KeyError, TypeError) as exc:
        raise _refuse("image tar index.json is malformed") from exc
    if not isinstance(entries, list) or len(entries) != 1:
        raise _refuse("image tar must hold exactly one manifest")
    (entry,) = entries
    if isinstance(entry, dict) and "vnd.docker.reference.type" in (entry.get("annotations") or {}):
        raise _refuse("image tar holds an attestation manifest")
    m_digest, m_size = _descriptor(entry, _MANIFEST_TYPES, "manifest")
    manifest = _locate(scan.blobs, m_digest, m_size)
    doc = _manifest_json(tar, manifest, entry["mediaType"])
    wanted = [_descriptor(doc.get("config"), _CONFIG_TYPES, "config")]
    wanted += [_descriptor(layer, _LAYER_TYPES, "layer") for layer in doc["layers"]]
    if wanted[0][1] > MAX_JSON_BYTES:
        raise _refuse("image config is too large")
    blobs = {d: _locate(scan.blobs, d, s) for d, s in wanted}
    for blob in blobs.values():
        verify_blob(tar, blob)
    return ImagePlan(manifest, entry["mediaType"], _read(tar, manifest), tuple(blobs.values()))
