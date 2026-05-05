"""History, checkpoints, rewind, and todos endpoints."""

import json

from fastapi import APIRouter, HTTPException
from loguru import logger

from config import get_settings
from schemas import RewindRequest
from session import sanitize_history, sessions

settings = get_settings()

router = APIRouter()


@router.get("/agents/{hosted_id}/history")
async def get_session_history(hosted_id: str):
    """Get the current message_history for persistence."""
    session = sessions.get(hosted_id)
    if not session:
        return {"history": []}
    try:
        # Sanitize before persistence so orphan ToolCallParts (from
        # aborted streams) don't trigger synthetic "Tool call was
        # cancelled." injection on next restore.
        clean = sanitize_history(session.message_history)[-30:]
        serialized = []
        for msg in clean:
            if isinstance(msg, dict):
                serialized.append(msg)
            elif hasattr(msg, "model_dump"):
                serialized.append(msg.model_dump(mode="json"))
            elif hasattr(msg, "__dict__"):
                serialized.append(msg.__dict__)
        return {"history": serialized}
    except Exception as e:
        logger.warning("History serialization error for {}: {}", hosted_id, e)
        return {"history": []}


def _resolve_checkpoint_store(session) -> object | None:
    """Find the CheckpointStore that pydantic-deep is using for this agent.

    pydantic-deep wires the store in two places:
      * the ``CheckpointToolset`` keeps a fallback reference under
        ``_fallback_store``;
      * if the caller injected one, it lives at ``deps.checkpoint_store``.

    Both are private contracts of the library, so try ``deps`` first
    (fewer assumptions) and fall back to scanning ``agent.toolsets``
    for an instance that exposes ``_fallback_store``.
    """
    deps_store = getattr(session.deps, "checkpoint_store", None)
    if deps_store is not None:
        return deps_store
    toolsets = getattr(session.agent, "toolsets", None) or []
    for ts in toolsets:
        candidate = getattr(ts, "_fallback_store", None)
        if candidate is not None:
            return candidate
    return None


@router.get("/agents/{hosted_id}/checkpoints")
async def list_checkpoints(hosted_id: str):
    """List available checkpoints for rewind."""
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(404, "Agent not running")
    try:
        store = _resolve_checkpoint_store(session)
        if store is None:
            return {"checkpoints": []}
        cp_list = await store.list_all()
        cps = []
        for cp in cp_list:
            created_at = getattr(cp, "created_at", None)
            cps.append({
                "id": getattr(cp, "id", ""),
                "label": getattr(cp, "label", ""),
                "turn": getattr(cp, "turn", 0),
                "message_count": getattr(cp, "message_count", 0),
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else ""),
            })
        return {"checkpoints": cps}
    except Exception as e:
        logger.warning("Checkpoint list error for {}: {}", hosted_id, e)
        return {"checkpoints": []}


@router.post("/agents/{hosted_id}/rewind")
async def rewind_to_checkpoint(hosted_id: str, body: RewindRequest):
    """Rewind agent to a previous checkpoint."""
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(404, "Agent not running")
    try:
        store = _resolve_checkpoint_store(session)
        if store is None:
            raise HTTPException(400, "Checkpoints not available")
        cp = await store.get(body.checkpoint_id)
        if cp is None:
            raise HTTPException(404, "Checkpoint not found")
        session.message_history = list(getattr(cp, "messages", []) or [])
        logger.info("Rewound agent {} to checkpoint {}", hosted_id, body.checkpoint_id)
        return {"status": "ok", "checkpoint_id": body.checkpoint_id, "message_count": len(session.message_history)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rewind error for {}: {}", hosted_id, e)
        raise HTTPException(500, str(e))


@router.get("/agents/{hosted_id}/todos")
async def get_todos(hosted_id: str):
    """Get agent's current todo list."""
    session = sessions.get(hosted_id)
    if not session:
        return {"todos": []}
    workspace = settings.workspace_root / hosted_id
    todos_file = workspace / "todos.json"
    if todos_file.exists():
        try:
            return {"todos": json.loads(todos_file.read_text())}
        except Exception:
            pass
    return {"todos": []}
