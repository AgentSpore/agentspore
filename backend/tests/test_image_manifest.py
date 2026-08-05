"""Guards the operator scripts that must exist inside the production image.

`backfill_contender_elo.py` was documented as runnable in the container and was
not in the image at all: `backend/Dockerfile` copied only `app/`, and
`deploy/docker-compose.prod.yml` bind-mounts the repo-root `scripts/` over
`/app/scripts`, so a naive copy would have been shadowed at runtime anyway.
Both failure modes are silent until an operator needs the script on a bad day,
so they are asserted here instead of in a Docker-dependent tier.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DOCKERFILE = BACKEND_DIR / "Dockerfile"
DOCKERIGNORE = BACKEND_DIR / ".dockerignore"
COMPOSE = REPO_ROOT / "deploy" / "docker-compose.prod.yml"

# Scripts an operator runs inside the running container against production data.
# Dev-time tools (probe_openrouter_models.py, redteam/) are deliberately absent:
# they need no production database and have no business in a shipped image.
REQUIRED_OPERATOR_SCRIPTS = ("scripts/backfill_contender_elo.py",)

_COPY = re.compile(r"^\s*COPY\s+(?P<args>.+?)\s*$", re.MULTILINE)
_MOUNT = re.compile(r"^\s*-\s+[^\s:]+:(?P<target>/[^\s:]+)(?::[a-z,]+)?\s*$", re.MULTILINE)


def _copy_pairs() -> list[tuple[str, str]]:
    """(source, destination) for every COPY in the backend Dockerfile."""
    pairs = []
    for match in _COPY.finditer(DOCKERFILE.read_text()):
        parts = [p for p in match.group("args").split() if not p.startswith("--")]
        if len(parts) >= 2:
            pairs.extend((source, parts[-1]) for source in parts[:-1])
    return pairs


def _image_path(source: str, destination: str) -> str:
    """Absolute in-image path a COPY of `source` lands on (WORKDIR is /app)."""
    target = destination if destination.startswith("/") else f"/app/{destination}"
    if target.endswith("/"):
        target += Path(source).name
    return posixpath.normpath(target)


def _shipped() -> dict[str, str]:
    """Repo-relative script path -> the absolute path it occupies in the image."""
    shipped = {}
    for source, destination in _copy_pairs():
        for script in REQUIRED_OPERATOR_SCRIPTS:
            if source == script or source.rstrip("/") == str(Path(script).parent):
                shipped[script] = _image_path(source, destination)
    return shipped


def test_operator_scripts_are_copied_into_the_image() -> None:
    missing = set(REQUIRED_OPERATOR_SCRIPTS) - set(_shipped())
    assert not missing, f"backend/Dockerfile has no COPY for: {sorted(missing)}"


def test_operator_scripts_exist_on_disk() -> None:
    for script in REQUIRED_OPERATOR_SCRIPTS:
        assert (BACKEND_DIR / script).is_file(), f"{script} is declared shipped but absent"


def test_operator_scripts_are_not_shadowed_by_a_bind_mount() -> None:
    mounts = _MOUNT.findall(COMPOSE.read_text())
    for script, image_path in _shipped().items():
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
    for script in REQUIRED_OPERATOR_SCRIPTS:
        parts = Path(script).parts
        candidates = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        for pattern in patterns:
            bare = pattern.strip("/")
            assert not any(fnmatch.fnmatch(c, bare) for c in candidates), (
                f".dockerignore pattern {pattern!r} excludes {script}"
            )
