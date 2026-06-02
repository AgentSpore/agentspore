"""Git workspace helpers for the Agent Runner service."""

import hashlib
import os
import subprocess
from pathlib import Path

from fastapi import HTTPException
from loguru import logger


# Files larger than this are reported but content is omitted from sync.
# Cap on the runner side mirrors the BE/FE truncation flag so a 50MB log
# produced inside the sandbox doesn't OOM the platform DB.
MAX_SYNC_BYTES = 500_000

# Regenerable/derived dirs pruned from traversal for performance; never hand-edited.
# Canonical set shared by list/download endpoints and tests.
# Dotfiles that ARE hand-edited (.env, .gitignore, .deep/) are intentionally absent.
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".pip",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def _file_version(path: Path) -> str:
    """Return a 12-hex-char SHA-256 content hash of ``path``.

    Works for both text and binary files — operates on raw bytes so
    encoding is not relevant. Stable: same bytes → same version token.

    Args:
        path: Absolute path to the file to hash.

    Returns:
        First 12 characters of the hex SHA-256 digest.
    """
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest()[:12]


def _run_git(
    cwd: Path, *args: str, timeout: float = 10.0
) -> subprocess.CompletedProcess:
    """Run ``git`` in ``cwd`` without raising on non-zero exit.

    Keeps stderr on the returned object so callers can log failures.
    All git calls use ``-c``  to disable global hooks / signing so a
    misconfigured host environment cannot poison an agent's workspace.
    """
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "AgentSpore",
        "GIT_AUTHOR_EMAIL": "agent@agentspore.local",
        "GIT_COMMITTER_NAME": "AgentSpore",
        "GIT_COMMITTER_EMAIL": "agent@agentspore.local",
    }
    # ``-c safe.directory=*`` disables git's ownership check so the
    # runner (uid 0 inside the container) can operate on workspaces
    # owned by the sandbox user (uid 1000 after the runner chowns them
    # for the sandbox container). Without this git refuses with
    # "fatal: detected dubious ownership" and every /diff call returns
    # an empty payload.
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _init_workspace_git(workspace: Path) -> None:
    """Seed the hosted workspace as a git repo so ``/diff`` can show
    pending file changes for review.

    Idempotent — if ``.git`` already exists we skip. The initial commit
    snapshots everything the runner wrote (AGENT.md, SKILL.md, seeded
    files, skills) so subsequent agent edits show up as a diff against
    that baseline. No-op when git is not installed on the runner host.
    """
    try:
        if (workspace / ".git").exists():
            return
        init = _run_git(workspace, "init", "-q", "-b", "main")
        if init.returncode != 0:
            logger.warning("git init failed in {}: {}", workspace, init.stderr.strip())
            return
        # .gitignore: don't track package noise dirs.
        (workspace / ".gitignore").write_text(
            "\n".join(
                [
                    "*.pyc",
                    "__pycache__/",
                    "node_modules/",
                    ".venv/",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        add = _run_git(workspace, "add", "-A")
        if add.returncode != 0:
            logger.warning("git add failed in {}: {}", workspace, add.stderr.strip())
            return
        commit = _run_git(
            workspace, "commit", "-q", "-m", "baseline: workspace initialised"
        )
        if commit.returncode != 0:
            logger.warning(
                "git baseline commit failed in {}: {}", workspace, commit.stderr.strip()
            )
    except FileNotFoundError:
        logger.warning("git binary not found on runner host — diff feature disabled")
    except subprocess.TimeoutExpired:
        logger.warning("git init timed out in {}", workspace)
    except Exception as exc:  # defensive — git setup must never break start
        logger.warning("git init unexpected error in {}: {}", workspace, exc)


def _status_label(state: str) -> str:
    if "D" in state:
        return "deleted"
    if "A" in state:
        return "added"
    if "R" in state:
        return "renamed"
    return "modified"


def _synthetic_add_patch(path: str, content: str) -> str:
    """Render an untracked file as a unified diff against /dev/null.

    ``git diff HEAD`` doesn't include untracked paths; we synthesise an
    equivalent patch so the UI's diff rendering can stay uniform.
    """
    lines = content.splitlines()
    header = (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
    )
    body = "\n".join(f"+{ln}" for ln in lines)
    return header + body + ("\n" if body else "")


def _safe_workspace_path(workspace: Path, file_path: str) -> Path:
    """Resolve ``file_path`` relative to ``workspace``, blocking traversal.

    Symlink-aware via ``resolve()``: even a clever symlink that points
    outside the workspace will fail the ``is_relative_to`` check.
    """
    if not file_path or file_path.startswith("/") or ".." in Path(file_path).parts:
        raise HTTPException(400, "Invalid file path")
    target = (workspace / file_path).resolve()
    workspace_resolved = workspace.resolve()
    try:
        target.relative_to(workspace_resolved)
    except ValueError:
        raise HTTPException(403, "Path escapes workspace")
    return target
