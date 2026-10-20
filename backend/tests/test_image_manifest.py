"""Keep development scripts out of the production backend image."""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND_DIR / "Dockerfile"

_COPY_VERBS = {"COPY", "ADD"}
# A source normalizing to one of these copies the whole build context.
_CONTEXT_ROOTS = {".", "/", ""}


def _instructions() -> list[tuple[str, str]]:
    """(verb, arguments) per Dockerfile instruction, backslash continuations joined."""
    joined = re.sub(r"\\\s*\n\s*", " ", DOCKERFILE.read_text())
    out = []
    for line in joined.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            verb, _, rest = stripped.partition(" ")
            out.append((verb.upper(), rest.strip()))
    return out


def _arguments(verb: str, rest: str) -> tuple[list[str], list[str]]:
    """(flags, paths) of a COPY/ADD, JSON-array form flattened.

    An interpolated path is a hard failure rather than a miss: it is exactly the
    case this reader cannot resolve, and a silent miss would wave it through.
    """
    remainder = rest.strip()
    flags = []
    while remainder.startswith("--"):
        flag, _, remainder = remainder.partition(" ")
        flags.append(flag)
        remainder = remainder.strip()
    if remainder.startswith("["):
        try:
            paths = [str(p) for p in json.loads(remainder)]
        except json.JSONDecodeError as exc:
            raise AssertionError(f"backend/Dockerfile: unreadable {verb} {rest!r} ({exc})") from exc
    else:
        paths = remainder.split()
    for path in paths:
        assert "$" not in path, (
            f"backend/Dockerfile: {verb} names the interpolated path {path!r}, which this "
            f"reader cannot resolve. Spell the path out, or teach this test the variable."
        )
    return flags, paths


def _stages() -> list[dict]:
    """One entry per build stage, in order, each carrying its WORKDIR and COPY list."""
    stages: list[dict] = []
    for verb, rest in _instructions():
        if verb == "FROM":
            tokens = [t for t in rest.split() if not t.startswith("--")]
            name = next(
                (tokens[i + 1] for i, t in enumerate(tokens[:-1]) if t.upper() == "AS"), None
            )
            stages.append({"name": name, "workdir": "/", "copies": []})
        elif not stages:
            continue
        elif verb == "WORKDIR":
            stages[-1]["workdir"] = posixpath.normpath(posixpath.join(stages[-1]["workdir"], rest))
        elif verb in _COPY_VERBS:
            flags, paths = _arguments(verb, rest)
            source_stage = next(
                (f.split("=", 1)[1] for f in flags if f.startswith("--from=")), None
            )
            if len(paths) > 1:
                stages[-1]["copies"] += [(source_stage, s, paths[-1]) for s in paths[:-1]]
    return stages


def _stage_index(stages: list[dict], reference: str, limit: int) -> int | None:
    """Index of the stage a `--from=` names, by ordinal or by name as Docker matches it."""
    if reference.isdigit():
        index = int(reference)
        return index if index < limit else None
    lowered = reference.lower()
    return next(
        (i for i, s in enumerate(stages[:limit]) if (s["name"] or "").lower() == lowered), None
    )


def _tail(source: str, candidate: str) -> str | None:
    """`candidate` relative to `source`, or None when `source` does not cover it.

    An empty string means the source names the file itself; anything else means
    the source is a directory, whose CONTENTS land in the destination.
    """
    normalized = source if source.startswith("/") else posixpath.normpath(source)
    if normalized in _CONTEXT_ROOTS:
        # `COPY . .` — the whole build context, the shape that ships everything.
        return candidate.lstrip("/")
    if normalized == candidate:
        return ""
    prefix = normalized.rstrip("/") + "/"
    return candidate[len(prefix) :] if candidate.startswith(prefix) else None


def _destination(workdir: str, destination: str, name: str, tail: str) -> str:
    base = destination if destination.startswith("/") else posixpath.join(workdir, destination)
    if tail:
        return posixpath.normpath(posixpath.join(base, tail))
    if base.endswith("/"):
        return posixpath.normpath(posixpath.join(base, name))
    return posixpath.normpath(base)


def _locate(stages: list[dict], index: int, script: str) -> list[str]:
    """Every absolute path `script` occupies inside stage `index`."""
    landings: list[str] = []
    name = Path(script).name
    for source_stage, source, destination in stages[index]["copies"]:
        if source_stage is None:
            origins = [script]
        else:
            prior = _stage_index(stages, source_stage, index)
            origins = [] if prior is None else _locate(stages, prior, script)
        for origin in origins:
            tail = _tail(source, origin)
            if tail is not None:
                landings.append(_destination(stages[index]["workdir"], destination, name, tail))
    return landings


def _in_final_image(script: str) -> list[str]:
    stages = _stages()
    return _locate(stages, len(stages) - 1, script) if stages else []


def _unlisted_scripts() -> list[str]:
    """Derive every development script from disk so new scripts stay protected."""
    found = (BACKEND_DIR / "scripts").rglob("*")
    return sorted(
        p.relative_to(BACKEND_DIR).as_posix()
        for p in found
        if p.is_file() and "__pycache__" not in p.parts
    )


def test_development_scripts_never_reach_the_final_image() -> None:
    """Reject broad copies that accidentally ship development-only scripts."""
    for script in _unlisted_scripts():
        landings = _in_final_image(script)
        assert not landings, (
            f"{script} is a development script yet would ship at {landings}. "
            f"Narrow the COPY in backend/Dockerfile."
        )
