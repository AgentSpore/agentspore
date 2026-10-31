"""Trusted-worker source fetch: one exact commit, no credentials, hard caps, no escaping links."""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.services.deployment.errors import DeployRejectedError
from app.services.deployment.process import run_tool

_SHA = re.compile(r"[0-9a-f]{40}")
_STRIPPED_FILES = ("*.pem", "*.key", "id_*", ".env", ".env.*")


@dataclass(frozen=True)
class FetchPolicy:
    max_files: int = 20_000
    max_bytes: int = 200 * 1024 * 1024
    timeout: float = 120.0
    schemes: tuple[str, ...] = ("https",)


DEFAULT_POLICY = FetchPolicy()


def _validate(repo_url: str, commit_sha: str, policy: FetchPolicy) -> None:
    parts = urlsplit(repo_url)
    if (
        parts.scheme not in policy.schemes
        or parts.username
        or parts.password
        or repo_url.startswith("-")
    ):
        raise DeployRejectedError(
            "source_invalid", "repository url must be credential-free " + "/".join(policy.schemes)
        )
    if _SHA.fullmatch(commit_sha) is None:
        raise DeployRejectedError("source_invalid", "commit_sha must be 40 lower-case hex")


async def _git(args: list[str], dest: Path, policy: FetchPolicy, scheme: str = "https") -> bytes:
    argv = [
        "git",
        "-c",
        "protocol.allow=never",
        "-c",
        f"protocol.{scheme}.allow=always",
        "-c",
        "core.hooksPath=/dev/null",
        *args,
    ]
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    rc, out = await run_tool(argv, cwd=dest, timeout=policy.timeout, env=env)
    if rc != 0:
        raise DeployRejectedError("source_fetch_failed", f"git {args[0]} exited {rc}")
    return out


def _check_caps(listing: bytes, policy: FetchPolicy) -> None:
    files, total = 0, 0
    for line in listing.splitlines():
        meta, _, _ = line.partition(b"\t")
        size = meta.split()[-1]
        files += 1
        total += int(size) if size.isdigit() else 0
        if files > policy.max_files or total > policy.max_bytes:
            raise DeployRejectedError(
                "source_too_large", f"over {policy.max_files} files or {policy.max_bytes} bytes"
            )


def _sanitize(root: Path) -> None:
    """Drop `.git` and key material; refuse any link that resolves outside `root`."""
    shutil.rmtree(root / ".git", ignore_errors=True)
    resolved_root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            if not path.resolve().is_relative_to(resolved_root):
                raise DeployRejectedError(
                    "source_unsafe", f"link escapes the source: {path.relative_to(root)}"
                )
        elif path.is_file() and any(path.match(pattern) for pattern in _STRIPPED_FILES):
            path.unlink()


async def fetch_source(
    repo_url: str, commit_sha: str, dest: Path, policy: FetchPolicy = DEFAULT_POLICY
) -> None:
    """Shallow-fetch `commit_sha` of `repo_url` into `dest` (created here, removed on refusal)."""
    _validate(repo_url, commit_sha, policy)
    dest.mkdir(parents=True)
    scheme = urlsplit(repo_url).scheme
    try:
        await _git(["init", "-q"], dest, policy, scheme)
        await _git(
            [
                "fetch",
                "-q",
                "--depth",
                "1",
                "--no-tags",
                "--no-recurse-submodules",
                repo_url,
                commit_sha,
            ],
            dest,
            policy,
            scheme,
        )
        _check_caps(await _git(["ls-tree", "-r", "-l", "FETCH_HEAD"], dest, policy, scheme), policy)
        await _git(["checkout", "-q", "--detach", "FETCH_HEAD"], dest, policy, scheme)
        _sanitize(dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
