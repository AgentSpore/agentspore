"""Drift guard: runner HIDDEN_PATH_PREFIXES vs BE PRUNE_PROTECTED_PREFIXES.

agent-runner/main.py and backend/app/repositories/hosted_agent_repo.py each
define their own copy of path-prefix constants (they live in separate Python
services that cannot import each other). This test parses both source files
via the AST -- no imports, no DB, no Docker, no network required.

It asserts that the BE constant is a superset of the runner constant, so CI
catches any drift before it ships.
"""

import ast
import pathlib
from typing import Any

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent  # agentsspore/
_RUNNER_FILE = _REPO_ROOT / "agent-runner" / "main.py"
_BE_REPO_FILE = (
    _REPO_ROOT
    / "backend"
    / "app"
    / "repositories"
    / "hosted_agent_repo.py"
)


def _extract_constant(source: str, name: str) -> set[str]:
    """Return the string elements of a module-level or class-body tuple/list/set assignment.

    Handles both module-level assignments and class-body assignments
    (``ClassName.ATTR = (...)`` or ``ATTR = (...)`` inside a class body).
    Supports string-literal elements only -- sufficient for path-prefix constants.
    Raises ``KeyError`` if the name is not found anywhere in the source.
    """
    tree = ast.parse(source)

    def _elts_to_set(value: Any) -> set[str]:
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return {
                elt.value
                for elt in value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
        return set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                # Module-level: NAME = (...)
                if isinstance(target, ast.Name) and target.id == name:
                    result = _elts_to_set(node.value)
                    if result is not None:
                        return result
                # Class-body: self.NAME or ClassName.NAME -- skip (handled below)
        # Class-body attribute: ATTR = (...) inside ClassDef
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == name:
                            return _elts_to_set(item.value)

    raise KeyError(f"Could not find assignment '{name}' in source")


def test_runner_file_exists() -> None:
    """Sanity: agent-runner/main.py is reachable from the repo root."""
    assert _RUNNER_FILE.exists(), (
        f"agent-runner/main.py not found at {_RUNNER_FILE}. "
        "Update _RUNNER_FILE in this test if the file was moved."
    )


def test_be_hosted_agent_repo_file_exists() -> None:
    """Sanity: backend hosted_agent_repo.py is reachable."""
    assert _BE_REPO_FILE.exists(), (
        f"hosted_agent_repo.py not found at {_BE_REPO_FILE}. "
        "Update _BE_REPO_FILE in this test if the file was moved."
    )


def test_be_prune_prefixes_is_superset_of_runner_hidden_prefixes() -> None:
    """BE PRUNE_PROTECTED_PREFIXES must be a superset of runner HIDDEN_PATH_PREFIXES.

    The runner controls which paths are reported to the platform; paths in
    HIDDEN_PATH_PREFIXES are never enumerated in /files responses. The BE must
    never prune those paths, otherwise rows that are legitimately present on
    disk (e.g. ``.deep/memory/``) get wiped on every sync because the runner
    withholds them and they always look "missing".

    If the runner adds a new hidden prefix, this test fails until the BE
    constant is updated -- forcing a conscious decision at review time.
    """
    runner_src = _RUNNER_FILE.read_text(encoding="utf-8")
    be_src = _BE_REPO_FILE.read_text(encoding="utf-8")

    runner_prefixes = _extract_constant(runner_src, "HIDDEN_PATH_PREFIXES")
    be_prefixes = _extract_constant(be_src, "PRUNE_PROTECTED_PREFIXES")

    missing = runner_prefixes - be_prefixes
    assert not missing, (
        f"runner HIDDEN_PATH_PREFIXES has entries absent from "
        f"HostedAgentRepo.PRUNE_PROTECTED_PREFIXES: {sorted(missing)}\n"
        f"Add them to backend/app/repositories/hosted_agent_repo.py."
    )
