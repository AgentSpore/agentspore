"""Bound explicit UTF-8 publication inputs; never inspect the user's filesystem."""

import hashlib
import json
import unicodedata
from typing import TypedDict


class FileDigest(TypedDict):
    """Describe reviewed content without retaining its body."""

    path: str
    bytes: int
    content_digest: str
    blob_sha: str


class Manifest(TypedDict):
    """Bind an ordered set of paths to exact UTF-8 bytes."""

    files: list[FileDigest]
    bytes: int
    digest: str


def canonical_digest(value: object) -> str:
    """Hash canonical JSON for caller-owned, non-secret intent metadata."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_path(path: str) -> None:
    """Reject noncanonical paths and credential or automation destinations."""
    if not isinstance(path, str) or not 1 <= len(path) <= 200:
        raise ValueError("publication_path_invalid")
    parts = path.casefold().split("/")
    forbidden = {
        ".git",
        ".ssh",
        ".aws",
        ".codex",
        ".claude",
        ".circleci",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "jenkinsfile",
        "azure-pipelines.yml",
        "bitbucket-pipelines.yml",
    }
    if (
        "\\" in path
        or ":" in path
        or any(unicodedata.category(c).startswith("C") for c in path)
        or any(p in {"", ".", ".."} or p in forbidden for p in parts)
        or any(
            p.startswith((".env", "id_rsa", "id_ed25519"))
            or "credential" in p
            or "privatekey" in p
            or "private_key" in p
            or "private-key" in p
            or p.endswith((".pem", ".key", ".p12", ".pfx"))
            for p in parts
        )
        or parts[:2] == [".github", "workflows"]
        or ".gitlab-ci.yml" in parts
    ):
        raise ValueError("publication_path_invalid")
    path.encode("utf-8")


def build_manifest(files: list[dict[str, str]]) -> Manifest:
    """Validate explicit files and hash bytes; this does not guarantee secret absence."""
    if not isinstance(files, list) or not 1 <= len(files) <= 32:
        raise ValueError("publication_file_count_invalid")
    entries: list[FileDigest] = []
    seen: set[str] = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ValueError("publication_file_invalid")
        path, content = item["path"], item["content"]
        validate_path(path)
        if path in seen or not isinstance(content, str):
            raise ValueError("publication_file_invalid")
        seen.add(path)
        raw = content.encode("utf-8")
        total += len(raw)
        if len(raw) > 262144 or total > 1048576:
            raise ValueError("publication_content_too_large")
        entries.append(
            {
                "path": path,
                "bytes": len(raw),
                "content_digest": hashlib.sha256(raw).hexdigest(),
                "blob_sha": hashlib.sha1(
                    b"blob " + str(len(raw)).encode() + b"\0" + raw,
                    usedforsecurity=False,
                ).hexdigest(),
            }
        )
    entries.sort(key=lambda entry: entry["path"])
    return {"files": entries, "bytes": total, "digest": canonical_digest(entries)}
