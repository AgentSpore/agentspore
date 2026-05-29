"""Workspace file endpoints: list, write, delete, diff, download."""

import asyncio
import io
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from config import get_settings
from schemas import WriteFileRequest
from workspace import (
    IGNORED_DIRS,
    MAX_SYNC_BYTES,
    _file_version,
    _run_git,
    _safe_workspace_path,
    _status_label,
    _synthetic_add_patch,
)

settings = get_settings()

router = APIRouter()

# disk_quota is injected by main.py after initialisation to avoid circular import.
disk_quota = None


def _iter_workspace_files(
    workspace: os.PathLike, *, include_hidden: bool
) -> list[tuple[str, str]]:
    """Walk ``workspace`` and return sorted ``(rel_path, abs_path)`` pairs.

    When ``include_hidden=False`` (default) we prune ``IGNORED_DIRS`` at the
    ``os.walk`` level — the OS never descends into those subtrees, which is
    the key perf win vs. ``rglob("*")`` + post-filter.  Dotfiles and
    dot-dirs that are NOT in ``IGNORED_DIRS`` (e.g. ``.env``, ``.gitignore``,
    ``.deep/``) are always included regardless of this flag.

    Args:
        workspace: Absolute path to the agent workspace root.
        include_hidden: When ``True`` walk everything (no pruning).

    Returns:
        List of ``(rel_path_str, abs_path_str)`` sorted by rel_path.
    """
    workspace = os.fspath(workspace)
    entries: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        if not include_hidden:
            # In-place prune: os.walk will not descend into removed names.
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        else:
            dirnames.sort()
        for filename in sorted(filenames):
            abs_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(abs_path, workspace)
            entries.append((rel, abs_path))
    entries.sort(key=lambda t: t[0])
    return entries


@router.get("/agents/{hosted_id}/files")
async def list_workspace_files(
    hosted_id: str,
    include_hidden: bool = False,
):
    """List all files in the agent's workspace (disk, not DB).

    Files are streamed back with content when small and decodable; for
    binary or oversize files we send ``content=None`` and a flag so the
    platform side can flag the row in DB instead of silently dropping it.
    Each file entry includes a ``version`` field (SHA-256[:12] of raw bytes).

    Query params:
        include_hidden: When ``True``, include ``IGNORED_DIRS`` subtrees
            (e.g. ``node_modules/``, ``.venv/``).  Default ``False``.
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        return {"files": []}

    files = []
    for rel, abs_path in _iter_workspace_files(
        workspace, include_hidden=include_hidden
    ):
        path = Path(abs_path)
        try:
            stat = path.stat()
            stat_size = stat.st_size
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
        try:
            version = _file_version(path)
        except OSError:
            version = None
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        files.append(
            {
                "file_path": rel,
                "content": content,
                "size_bytes": stat_size,
                "truncated": truncated,
                "is_binary": is_binary,
                "version": version,
                "modified_at": modified_at,
            }
        )
    return {"files": files}


# NOTE: /files/download must be registered BEFORE /files/{file_path:path}
# so that the literal "download" segment is not swallowed by the path wildcard.
@router.get("/agents/{hosted_id}/files/download")
async def download_workspace_zip(
    hosted_id: str,
    include_hidden: bool = False,
):
    """Stream the agent workspace as a ZIP archive.

    All files except ``IGNORED_DIRS`` subtrees are included by default —
    binary files are included as-is (no ``MAX_SYNC_BYTES`` cap here, this
    is raw export).  ZIP built in executor thread to avoid blocking the
    event loop; full archive held in memory (workspaces are small — code,
    not data).

    Query params:
        include_hidden: When ``True``, include ``IGNORED_DIRS`` subtrees.
            Default ``False``.
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        raise HTTPException(404, "Agent workspace not found")

    # Capture include_hidden for the closure (avoids cell-var-from-loop lint).
    _include_hidden = include_hidden

    def _build_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel, abs_path in _iter_workspace_files(
                workspace, include_hidden=_include_hidden
            ):
                try:
                    zf.write(abs_path, arcname=rel)
                except OSError as exc:
                    logger.warning("download_zip: skipping {} — {}", rel, exc)
        return buf.getvalue()

    # Build in a thread to avoid blocking the event loop on large workspaces.
    data = await asyncio.get_event_loop().run_in_executor(None, _build_zip)

    filename = f"workspace-{hosted_id}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/agents/{hosted_id}/files/{file_path:path}")
async def get_workspace_file(hosted_id: str, file_path: str):
    """Return metadata and (when feasible) content of a single workspace file.

    Returns:
        JSON with ``file_path``, ``content`` (``None`` for binary/oversize),
        ``size_bytes``, ``truncated``, ``is_binary``, ``version``.

    Raises:
        404 if the file does not exist.
        400/403 for invalid/traversal paths (raised by ``_safe_workspace_path``).
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        raise HTTPException(404, "Agent workspace not found")

    target = _safe_workspace_path(workspace, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")

    try:
        stat = target.stat()
        stat_size = stat.st_size
    except OSError as exc:
        raise HTTPException(500, f"Cannot stat file: {exc}") from exc

    truncated = stat_size > MAX_SYNC_BYTES
    is_binary = False
    content: str | None = None
    if not truncated:
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            is_binary = True
        except PermissionError:
            content = None

    version = _file_version(target)
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return {
        "file_path": file_path,
        "content": content,
        "size_bytes": stat_size,
        "truncated": truncated,
        "is_binary": is_binary,
        "version": version,
        "modified_at": modified_at,
    }


@router.put("/agents/{hosted_id}/files")
async def write_workspace_file(
    hosted_id: str,
    body: WriteFileRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    """Write or update a file on the agent's workspace disk.

    Mirrors what ``write_file`` in pydantic-deep does internally so a UI
    edit becomes visible to the running agent without restart. The file
    is also re-added to the git workspace so ``/diff`` reflects it.

    Optimistic concurrency (If-Match header):
        - If ``If-Match`` is provided and the file already exists, the
          current version is compared.  A mismatch returns **412** with
          ``current_version`` and ``current_content`` (``None`` for
          binary/oversize) so the caller can resolve the conflict.
        - If ``If-Match`` is absent the write proceeds unconditionally
          (backward-compatible behaviour).

    Returns:
        ``{status, file_path, size_bytes, version}`` on success.
    """
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        raise HTTPException(404, "Agent workspace not found")

    target = _safe_workspace_path(workspace, body.file_path)

    # Optimistic lock: check current version before any quota/write logic.
    if if_match is not None and target.exists() and target.is_file():
        current_version = _file_version(target)
        if current_version != if_match:
            # Read current content for conflict resolution (same rules as list).
            current_size = target.stat().st_size
            current_content: str | None = None
            if current_size <= MAX_SYNC_BYTES:
                try:
                    current_content = target.read_text(encoding="utf-8")
                except (UnicodeDecodeError, PermissionError):
                    current_content = None
            raise HTTPException(
                412,
                detail={
                    "message": "Precondition Failed: version mismatch",
                    "current_version": current_version,
                    "current_content": current_content,
                },
            )

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

    new_version = _file_version(target)
    return {
        "status": "written",
        "file_path": body.file_path,
        "size_bytes": len(body.content.encode("utf-8")),
        "version": new_version,
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

    status = _run_git(
        workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
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
            files.append(
                {
                    "path": path,
                    "status": "added",
                    "patch": _synthetic_add_patch(path, content),
                }
            )
            continue
        patch = _run_git(workspace, "diff", "HEAD", "--", path)
        files.append(
            {
                "path": path,
                "status": _status_label(state),
                "patch": patch.stdout if patch.returncode == 0 else "",
            }
        )
    return {"files": files, "git_available": True}
