"""Tests for /start no-clobber and git-guard behavior.

Scope: unit (tmp_path, no Docker, no real HTTP).
Covers:
  - Payload files that already exist on the persistent workspace are NOT overwritten.
  - Payload files that do NOT yet exist ARE written (cold workspace seed).
  - _init_workspace_git does NOT re-baseline an existing git repo.
  - _init_workspace_git DOES initialise a fresh workspace.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workspace import _init_workspace_git  # noqa: E402


# ---------------------------------------------------------------------------
# _init_workspace_git guard
# ---------------------------------------------------------------------------


class TestInitWorkspaceGit:
    def test_initialises_fresh_workspace(self, tmp_path: Path) -> None:
        """A workspace without .git gets initialised and a baseline commit is made."""
        _init_workspace_git(tmp_path)
        assert (tmp_path / ".git").exists(), ".git dir must exist after init"

    def test_no_rebasing_existing_repo(self, tmp_path: Path) -> None:
        """A workspace that already has .git is left untouched (no extra commits)."""
        # First init — baseline
        _init_workspace_git(tmp_path)

        # Write a working file and do NOT commit it
        working_file = tmp_path / "work.py"
        working_file.write_text("print('agent work')", encoding="utf-8")

        # Second call simulates a restart
        _init_workspace_git(tmp_path)

        # The working file must still be untracked (not committed by a re-baseline)
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "status", "--porcelain"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert "work.py" in result.stdout, (
            "Working file must remain untracked after second _init_workspace_git call; "
            f"got: {result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# No-clobber file write logic (extracted from routes/agents.py loop)
# ---------------------------------------------------------------------------


def _apply_payload_files(workspace: Path, files: list[dict]) -> None:
    """Replicate the payload-write loop from routes/agents.py start_agent.

    Only the no-clobber logic is under test; heavy imports (FastAPI, pydantic_ai,
    etc.) are deliberately avoided so the test suite stays unit-only.
    """
    skip_parts = {"venv", ".venv", "__pycache__", "node_modules"}
    for f in files:
        fp = f.get("file_path", "")
        if not fp:
            continue
        if skip_parts.intersection(PurePosixPath(fp).parts):
            continue
        file_path = workspace / fp
        if not str(file_path).startswith(str(workspace)):
            continue
        # No-clobber guard — same logic as production code
        if file_path.exists():
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f.get("content", "") or "", encoding="utf-8")


class TestNoClobberFileWrite:
    def test_cold_workspace_seeds_config_file(self, tmp_path: Path) -> None:
        """agent.yaml is written when workspace is fresh (file does not exist)."""
        payload = [{"file_path": "agent.yaml", "content": "include_todo: true\n"}]
        _apply_payload_files(tmp_path, payload)

        target = tmp_path / "agent.yaml"
        assert target.exists(), "agent.yaml must be seeded on cold workspace"
        assert target.read_text(encoding="utf-8") == "include_todo: true\n"

    def test_existing_working_file_is_not_overwritten(self, tmp_path: Path) -> None:
        """A file the agent wrote (already on disk) survives a restart payload."""
        # Pre-existing agent work
        working = tmp_path / "my_script.py"
        working.write_text("# agent wrote this\nprint('hello')", encoding="utf-8")
        original_content = working.read_text(encoding="utf-8")

        # Payload tries to overwrite with stale seed content
        payload = [{"file_path": "my_script.py", "content": "# stale seed"}]
        _apply_payload_files(tmp_path, payload)

        assert working.read_text(encoding="utf-8") == original_content, (
            "Existing working file must not be overwritten by restart payload"
        )

    def test_existing_config_file_is_not_overwritten(self, tmp_path: Path) -> None:
        """Custom agent.yaml edited by the owner is preserved across restart."""
        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text("include_todo: false\ncustom_flag: true\n", encoding="utf-8")
        original = yaml_file.read_text(encoding="utf-8")

        payload = [{"file_path": "agent.yaml", "content": "include_todo: true\n"}]
        _apply_payload_files(tmp_path, payload)

        assert yaml_file.read_text(encoding="utf-8") == original, (
            "Owner-customised agent.yaml must survive restart (no-clobber)"
        )

    def test_new_config_file_seeded_alongside_existing_working_file(self, tmp_path: Path) -> None:
        """Config seed is written for a new file while existing files stay untouched."""
        # Pre-existing working file
        work = tmp_path / "data.json"
        work.write_text('{"result": 42}', encoding="utf-8")

        # agent.yaml does NOT exist yet (cold workspace for this file)
        payload = [
            {"file_path": "agent.yaml", "content": "include_todo: true\n"},
            {"file_path": "data.json", "content": "{}"},
        ]
        _apply_payload_files(tmp_path, payload)

        assert (tmp_path / "agent.yaml").read_text(encoding="utf-8") == "include_todo: true\n"
        assert work.read_text(encoding="utf-8") == '{"result": 42}', (
            "data.json must not be clobbered by payload"
        )

    def test_skip_parts_dirs_are_ignored(self, tmp_path: Path) -> None:
        """Files inside venv/.venv/__pycache__/node_modules are never written."""
        payload = [
            {"file_path": "venv/bin/python", "content": "skip"},
            {"file_path": ".venv/lib/site.py", "content": "skip"},
            {"file_path": "__pycache__/mod.pyc", "content": "skip"},
        ]
        _apply_payload_files(tmp_path, payload)

        assert not (tmp_path / "venv").exists()
        assert not (tmp_path / ".venv").exists()
        assert not (tmp_path / "__pycache__").exists()
