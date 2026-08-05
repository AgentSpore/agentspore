"""Guards the operator scripts that must exist inside the production image.

`backfill_contender_elo.py` was documented as runnable in the container and was
not in the image at all: `backend/Dockerfile` copied only `app/`, and
`deploy/docker-compose.prod.yml` bind-mounts the repo-root `scripts/` over
`/app/scripts`, so a naive copy would have been shadowed at runtime anyway.
Both failure modes are silent until an operator needs the script on a bad day,
so they are asserted here instead of in a Docker-dependent tier.

The manifest pins the EXACT in-image path rather than "some COPY mentions it".
Three shapes defeated the weaker form: a copy into a build stage the final stage
never re-copies (the EE line builds this backend multi-stage), a backslash
continuation that made the destination parse as `\\`, and a retarget to another
directory that leaves the documented module name unimportable.
"""

from __future__ import annotations

import ast
import fnmatch
import posixpath
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DOCKERFILE = BACKEND_DIR / "Dockerfile"
DOCKERIGNORE = BACKEND_DIR / ".dockerignore"
COMPOSE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"

# Repo-relative script -> the absolute path it must occupy in the FINAL image.
# Dev-time tools (probe_openrouter_models.py, redteam/) are deliberately absent:
# they need no production database and have no business in a shipped image.
MANIFEST = {"scripts/backfill_contender_elo.py": "/app/ops_scripts/backfill_contender_elo.py"}

_MOUNT = re.compile(r"^\s*-\s+[^\s:]+:(?P<target>/[^\s:]+)(?::[a-z,]+)?\s*$", re.MULTILINE)


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


def _stages() -> list[dict]:
    """One entry per build stage, in order, each carrying its WORKDIR and COPY list."""
    stages: list[dict] = []
    for verb, rest in _instructions():
        if verb == "FROM":
            parts = rest.split()
            name = parts[2] if len(parts) > 2 and parts[1].upper() == "AS" else None
            stages.append({"name": name, "workdir": "/", "copies": []})
        elif not stages:
            continue
        elif verb == "WORKDIR":
            stages[-1]["workdir"] = posixpath.normpath(posixpath.join(stages[-1]["workdir"], rest))
        elif verb == "COPY":
            args = rest.split()
            flags = [a for a in args if a.startswith("--")]
            paths = [a for a in args if not a.startswith("--")]
            source_stage = next(
                (f.split("=", 1)[1] for f in flags if f.startswith("--from=")), None
            )
            if len(paths) > 1:
                stages[-1]["copies"] += [(source_stage, s, paths[-1]) for s in paths[:-1]]
    return stages


def _tail(source: str, candidate: str | None) -> str | None:
    """`candidate` relative to `source`, or None when `source` does not cover it.

    An empty string means the source names the file itself; anything else means
    the source is a directory, whose CONTENTS land in the destination.
    """
    if candidate is None:
        return None
    normalized = source if source.startswith("/") else posixpath.normpath(source)
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


def _locate(stages: list[dict], index: int, script: str) -> str | None:
    """Absolute path of `script` inside stage `index`, or None if it never arrives there."""
    found = None
    name = Path(script).name
    for source_stage, source, destination in stages[index]["copies"]:
        if source_stage is None:
            tail = _tail(source, script)
        else:
            prior = next(
                (i for i, s in enumerate(stages[:index]) if s["name"] == source_stage), None
            )
            tail = None if prior is None else _tail(source, _locate(stages, prior, script))
        if tail is not None:
            found = _destination(stages[index]["workdir"], destination, name, tail)
    return found


def _in_final_image(script: str) -> str | None:
    stages = _stages()
    return _locate(stages, len(stages) - 1, script) if stages else None


def test_operator_scripts_land_at_their_manifest_path_in_the_final_image() -> None:
    for script, expected in MANIFEST.items():
        actual = _in_final_image(script)
        assert actual == expected, (
            f"{script} must reach the LAST build stage at {expected}, found {actual}"
        )


def test_operator_scripts_exist_on_disk() -> None:
    for script in MANIFEST:
        assert (BACKEND_DIR / script).is_file(), f"{script} is declared shipped but absent"


def test_operator_scripts_are_not_shadowed_by_a_bind_mount() -> None:
    mounts = _MOUNT.findall(COMPOSE.read_text())
    for script, image_path in MANIFEST.items():
        for mount in mounts:
            assert not image_path.startswith(f"{mount.rstrip('/')}/"), (
                f"{script} lands on {image_path}, which the compose mount {mount} hides"
            )


def test_operator_scripts_survive_dockerignore() -> None:
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    ]
    for script in MANIFEST:
        parts = Path(script).parts
        candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        for pattern in patterns:
            bare = pattern.strip("/")
            # Docker lets a leading `**/` match zero directories, so `**/scripts`
            # excludes a top-level scripts/ that plain fnmatch would never match.
            forms = {bare, bare.removeprefix("**/")}
            assert not any(fnmatch.fnmatch(c, f) for c in candidates for f in forms), (
                f".dockerignore pattern {pattern!r} excludes {script}"
            )


def test_docstrings_document_the_module_name_the_image_actually_provides() -> None:
    """A correct path is worthless if the command the operator is told to type misses it."""
    for script, image_path in MANIFEST.items():
        package = posixpath.dirname(image_path).removeprefix("/app/").replace("/", ".")
        docstring = ast.get_docstring(ast.parse((BACKEND_DIR / script).read_text())) or ""
        wanted = f"-m {package}.{Path(script).stem}"
        assert wanted in " ".join(docstring.split()), (
            f"{script} ships at {image_path} but its docstring never documents {wanted!r}"
        )
