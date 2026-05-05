"""Workspace file endpoints: list, write, delete, diff."""

import asyncio
import os

from fastapi import APIRouter, HTTPException
from loguru import logger

from config import get_settings
from schemas import WriteFileRequest
from workspace import (
    MAX_SYNC_BYTES,
    _NOISE_DIRS,
    _run_git,
    _safe_workspace_path,
    _status_label,
    _synthetic_add_patch,
)

settings = get_settings()

router = APIRouter()

# disk_quota is injected by main.py after initialisation to avoid circular import.
disk_quota = None


@router.get("/agents/{hosted_id}/files")
async def list_workspace_files(hosted_id: str):
    """List all files in the agent's workspace (disk, not DB).

    Files are streamed back with content when small and decodable; for
    binary or oversize files we send ``content=None`` and a flag so the
    platform side can flag the row in DB instead of silently dropping it.
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        return {"files": []}

    files = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        # Skip package noise dirs anywhere in the tree.
        if _NOISE_DIRS.intersection(path.relative_to(workspace).parts):
            continue
        rel = str(path.relative_to(workspace))
        try:
            stat_size = path.stat().st_size
        except OSError:
            continue
        truncated = stat_size > MAX_SYNC_BYTES
        is_binary = False
        content: str | None = None
        if not truncated:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                is_binary = True
            except PermissionError:
                content = None
        files.append({
            "file_path": rel,
            "content": content,
            "size_bytes": stat_size,
            "truncated": truncated,
            "is_binary": is_binary,
        })
    return {"files": files}


@router.put("/agents/{hosted_id}/files")
async def write_workspace_file(hosted_id: str, body: WriteFileRequest):
    """Write or update a file on the agent's workspace disk.

    Mirrors what ``write_file`` in pydantic-deep does internally so a UI
    edit becomes visible to the running agent without restart. The file
    is also re-added to the git workspace so ``/diff`` reflects it.
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        raise HTTPException(404, "Agent workspace not found")

    target = _safe_workspace_path(workspace, body.file_path)

    # Quota enforcement: infrastructure paths (checkpoints/) bypass the
    # hard-limit block; all other agent-controlled writes are checked.
    if not disk_quota.is_checkpoint_path(body.file_path):
        usage_mb, allowed = await disk_quota.check_quota_async(hosted_id)
        if not allowed:
            raise HTTPException(
                507,
                f"Disk quota exceeded ({disk_quota.get_limits()[1]} MB). "
                "Delete files via the file ops tab to free space.",
            )
        soft_mb, _ = disk_quota.get_limits()
        if usage_mb >= soft_mb:
            asyncio.create_task(disk_quota.handle_soft_breach(hosted_id, usage_mb))
    # Invalidate cache after write so next check reflects real state.
    disk_quota.invalidate(hosted_id)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")

    # Mirror ownership semantics from start_agent so the sandboxed
    # container user can keep editing (or at least read) the file.
    try:
        if os.getuid() == 0:
            os.chown(target, 1000, 1000)
        else:
            os.chmod(target, 0o666)
    except OSError:
        pass

    return {
        "status": "written",
        "file_path": body.file_path,
        "size_bytes": len(body.content.encode("utf-8")),
    }


@router.delete("/agents/{hosted_id}/files/{file_path:path}")
async def delete_workspace_file(hosted_id: str, file_path: str):
    """Delete a file from the agent's workspace on disk."""
    workspace = settings.workspace_root / hosted_id
    target = _safe_workspace_path(workspace, file_path)
    if target.exists() and target.is_file():
        target.unlink()
        return {"status": "deleted"}
    return {"status": "not_found"}


@router.get("/agents/{hosted_id}/diff")
async def get_workspace_diff(hosted_id: str):
    """Return the list of files that changed since the workspace was
    initialised, with their unified diff patches.

    Uses ``git diff HEAD`` (the baseline commit + any later commits
    the agent made) so the user-visible "pending changes" set is what
    would be staged if the user hit a commit button.
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists() or not (workspace / ".git").exists():
        return {"files": [], "git_available": False}

    status = _run_git(workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status.returncode != 0:
        logger.warning("git status failed for {}: {}", hosted_id, status.stderr.strip())
        return {"files": [], "git_available": False}

    files: list[dict] = []
    # Parse porcelain -z: two-char status, space, filename, NUL.
    parts = [p for p in status.stdout.split("\0") if p]
    for raw in parts:
        if len(raw) < 3:
            continue
        state = raw[:2]
        path = raw[3:]
        index_state = state[0]
        worktree_state = state[1]
        if index_state == "?" or worktree_state == "?":
            # Untracked file — diff by reading contents against /dev/null.
            full = workspace / path
            try:
                content = full.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, FileNotFoundError):
                content = ""
            files.append({
                "path": path,
                "status": "added",
                "patch": _synthetic_add_patch(path, content),
            })
            continue
        patch = _run_git(workspace, "diff", "HEAD", "--", path)
        files.append({
            "path": path,
            "status": _status_label(state),
            "patch": patch.stdout if patch.returncode == 0 else "",
        })
    return {"files": files, "git_available": True}
