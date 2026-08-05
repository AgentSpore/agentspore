"""Guards the reproducibility of the backend image's dependency install.

The Dockerfile used to copy only `pyproject.toml` and run a bare `uv sync`, so
every build re-resolved against the network and generated its own throwaway
lockfile inside the image — while `backend/uv.lock` sat tracked in git, unused.
Two builds of the same commit could ship different dependency versions.

These assertions are cheap and need no Docker daemon; the real proof (installed
versions matching uv.lock, and the build failing on drift) is a manual build.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import shlex
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND_DIR / "Dockerfile"
DOCKERIGNORE = BACKEND_DIR / ".dockerignore"

_UV_SYNC = re.compile(r"^\s*RUN\s+.*\buv sync\b(?P<rest>.*)$", re.MULTILINE)
_CMD = re.compile(r"^\s*CMD\s+(?P<rest>.+)$", re.MULTILINE)
_WORKDIR = re.compile(r"^\s*WORKDIR\s+(?P<path>\S+)\s*$", re.MULTILINE)
_SHELLS = {"sh", "bash", "ash", "dash"}


def _dockerfile() -> str:
    """Dockerfile text with backslash continuations joined into single lines."""
    return re.sub(r"\\\s*\n\s*", " ", DOCKERFILE.read_text())


def test_lockfile_exists_and_is_copied_into_the_image() -> None:
    assert (BACKEND_DIR / "uv.lock").is_file(), "backend/uv.lock is the pin; it must exist"
    copies = re.findall(r"^\s*COPY\s+(?P<rest>.+)$", _dockerfile(), re.MULTILINE)
    assert any("uv.lock" in line for line in copies), (
        "backend/Dockerfile never copies uv.lock, so `uv sync` cannot honour it"
    )


def test_uv_sync_refuses_to_re_resolve() -> None:
    """`--locked` fails on drift; `--frozen` would silently install a stale lock."""
    syncs = _UV_SYNC.findall(_dockerfile())
    assert syncs, "backend/Dockerfile no longer runs `uv sync`; update this guard"
    for rest in syncs:
        assert "--locked" in rest.split(), (
            f"`uv sync{rest}` may re-resolve dependencies at build time. Pass --locked."
        )


def _venv_interpreter() -> str:
    """The interpreter `uv sync` creates, derived from the Dockerfile's own WORKDIR."""
    workdir = "/"
    for path in _WORKDIR.findall(_dockerfile()):
        workdir = posixpath.normpath(posixpath.join(workdir, path))
    return posixpath.join(workdir, ".venv/bin/python")


def _cmd_tokens(rest: str) -> list[str]:
    """The argv a CMD really execs, with a `sh -c` payload unwrapped into tokens."""
    remainder = rest.strip()
    if remainder.startswith("["):
        tokens = [str(t) for t in json.loads(remainder)]
    else:
        tokens = shlex.split(remainder)
    if tokens and Path(tokens[0]).name in _SHELLS and "-c" in tokens:
        payload = tokens[tokens.index("-c") + 1]
        return shlex.split(payload)
    return tokens


def test_start_command_execs_the_baked_venv_interpreter() -> None:
    """Asserted positively, not "unless it looks like uv run".

    A conditional guard passes vacuously on every shape it does not recognise: a
    typo'd interpreter path and a `sh -c "uv run ..."` wrapper both sailed through
    the earlier form. Pinning the exact argv[0] is the same bar test_image_manifest
    holds for in-image script paths — and `uv run` is excluded by construction,
    since it re-checks the environment (network) on every container start.
    """
    commands = _CMD.findall(_dockerfile())
    assert commands, "backend/Dockerfile has no CMD"
    expected = _venv_interpreter()
    for rest in commands:
        tokens = _cmd_tokens(rest)
        assert tokens and tokens[0] == expected, (
            f"CMD must exec {expected} (the venv `uv sync` builds), got {tokens[:1] or 'nothing'}. "
            f"`uv run` syncs on every start; another path is no real interpreter in this image."
        )


def test_lockfile_survives_dockerignore() -> None:
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    ]
    for pattern in patterns:
        bare = pattern.strip("/")
        forms = {bare, bare.removeprefix("**/")}
        assert not any(fnmatch.fnmatch("uv.lock", f) for f in forms), (
            f".dockerignore pattern {pattern!r} excludes uv.lock from the build context"
        )
