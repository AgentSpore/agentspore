"""Trusted-worker source fetch (spec §2): GitHub tarball of one commit, capped, plain files."""

import asyncio
import gzip
import re
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx
from loguru import logger

from app.services.deployment.errors import DeployRejectedError

_SHA = re.compile(r"[0-9a-f]{40}")
_REPO_PATH = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")
_CODELOAD = "https://codeload.github.com/{owner}/{repo}/tar.gz/{sha}"
_KEY_FILES = frozenset({"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"})
_ENV_KEPT = frozenset({".env.example", ".env.sample"})
_SCANNER_CONFIGS = frozenset({".gitleaks.toml", ".gitleaksignore", "osv-scanner.toml"})
_CHUNK = 64 * 1024
_HEADER_ALLOWANCE = 1536  # tar header + padding per member
_HEADROOM = 1024 * 1024  # pax/GNU extension headers


@dataclass(frozen=True)
class FetchPolicy:
    max_archive: int = 50 * 1024 * 1024
    max_bytes: int = 200 * 1024 * 1024
    max_files: int = 20_000
    timeout: float = 120.0
    extract_timeout: float = 60.0


DEFAULT_POLICY = FetchPolicy()


def is_stripped(name: str) -> bool:
    """Key material and scanner/ignore configs never reach the build context (any letter case)."""
    name = name.lower()
    if name in _KEY_FILES or name in _SCANNER_CONFIGS:
        return True
    if name == ".env" or (name.startswith(".env.") and name not in _ENV_KEPT):
        return True
    return name.endswith((".pem", ".key"))


def _archive_url(repo_url: str, commit_sha: str) -> str:
    parts = urlsplit(repo_url)
    match = _REPO_PATH.fullmatch(parts.path)
    if (
        parts.scheme != "https"
        or parts.netloc.lower() != "github.com"
        or parts.query
        or parts.fragment
        or match is None
        or set(match[1]) == {"."}
        or set(match[2]) == {"."}
        or _SHA.fullmatch(commit_sha) is None
    ):
        raise DeployRejectedError(
            "source_invalid", "need https://github.com/<owner>/<repo> and a 40-hex sha"
        )
    return _CODELOAD.format(owner=match[1], repo=match[2], sha=commit_sha)


async def _download(client: httpx.AsyncClient, url: str, target: Path, policy: FetchPolicy) -> None:
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise DeployRejectedError(
                    "source_fetch_failed", f"archive host answered {response.status_code}"
                )
            received = 0
            with target.open("wb") as out:
                async for chunk in response.aiter_bytes(_CHUNK):
                    received += len(chunk)
                    if received > policy.max_archive:
                        raise DeployRejectedError(
                            "source_too_large", f"archive over {policy.max_archive} bytes"
                        )
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise DeployRejectedError("source_fetch_failed", type(exc).__name__) from exc


def _relative(member: tarfile.TarInfo) -> PurePosixPath | None:
    """Path below the single top-level directory GitHub adds, or None for that directory itself."""
    parts = PurePosixPath(member.name).parts
    if member.name.startswith("/") or ".." in parts:
        raise DeployRejectedError("source_unsafe", f"unsafe path in archive: {member.name[:80]}")
    return PurePosixPath(*parts[1:]) if len(parts) > 1 else None


def _unpack(tar: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> bool:
    """Extract one plain file/dir below `dest`; True when a symlink was dropped instead."""
    relative = _relative(member)
    if member.issym():
        return True
    if not (member.isfile() or member.isdir()):
        raise DeployRejectedError(
            "source_unsafe", f"not a plain file or directory: {member.name[:80]}"
        )
    if relative is not None and not (member.isfile() and is_stripped(relative.name)):
        member.name = relative.as_posix()
        tar.extract(member, dest, filter="data")
    return False


class _BoundedReader:
    """Gunzipped archive stream: every read enforces the byte cap, deadline and stop flag.

    tarfile reads pax/GNU long-name headers inside its own header parsing, so only a bound on
    the raw stream keeps those from escaping `max_bytes` and the extraction deadline.
    """

    def __init__(self, source, cap: int, deadline: float, stop: threading.Event):
        self.source, self.cap, self.deadline, self.stop = source, cap, deadline, stop
        self.read_so_far = 0

    def check(self) -> None:
        if self.stop.is_set():
            raise DeployRejectedError("source_fetch_failed", "extraction cancelled")
        if time.monotonic() > self.deadline:
            raise DeployRejectedError("source_fetch_failed", "extraction timeout")

    def read(self, size: int) -> bytes:
        self.check()
        data = self.source.read(size)
        self.read_so_far += len(data)
        if self.read_so_far > self.cap:
            raise DeployRejectedError("source_too_large", "unpacked source over the size cap")
        return data


def _extract(archive: Path, dest: Path, policy: FetchPolicy, stop: threading.Event) -> None:
    files, total, dropped_links = 0, 0, 0
    cap = policy.max_bytes + policy.max_files * _HEADER_ALLOWANCE + _HEADROOM
    deadline = time.monotonic() + policy.extract_timeout
    try:
        with gzip.open(archive, "rb") as gz:
            reader = _BoundedReader(gz, cap, deadline, stop)
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    reader.check()
                    files += 1
                    total += member.size
                    if files > policy.max_files or total > policy.max_bytes:
                        raise DeployRejectedError(
                            "source_too_large", "unpacked source over the file or size cap"
                        )
                    dropped_links += _unpack(tar, member, dest)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise DeployRejectedError(
            "source_fetch_failed", f"unreadable archive: {type(exc).__name__}"
        ) from exc
    if dropped_links:
        logger.info("fetcher dropped {} symlink members", dropped_links)


async def _extract_in_thread(archive: Path, dest: Path, policy: FetchPolicy) -> None:
    """Run `_extract` in a worker thread; on cancel, stop it and wait before returning control."""
    stop = threading.Event()
    worker = asyncio.ensure_future(asyncio.to_thread(_extract, archive, dest, policy, stop))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        await asyncio.wait([worker])
        worker.exception()
        raise


async def _fetch(client: httpx.AsyncClient, url: str, dest: Path, policy: FetchPolicy) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "source.tar.gz"
        try:
            async with asyncio.timeout(policy.timeout):
                await _download(client, url, archive, policy)
        except TimeoutError as exc:
            raise DeployRejectedError("source_fetch_failed", "download timeout") from exc
        await _extract_in_thread(archive, dest, policy)


async def fetch_source(
    repo_url: str,
    commit_sha: str,
    dest: Path,
    policy: FetchPolicy = DEFAULT_POLICY,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Download and unpack `commit_sha` of a github.com repository into `dest`.

    `client` is the seam for authentication (a GitHub App token header) and for tests.
    `dest` is created here and removed on any refusal.
    """
    url = _archive_url(repo_url, commit_sha)
    dest.mkdir(parents=True)
    owned = client or httpx.AsyncClient(follow_redirects=False)
    try:
        await _fetch(owned, url, dest, policy)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        if client is None:
            await owned.aclose()
