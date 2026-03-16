"""
Privacy Mixer API — split sensitive tasks across agents
========================================================
User-facing (Bearer JWT):
  POST /mixer                              — create session (parse markers, encrypt fragments)
  GET  /mixer                              — list my sessions
  GET  /mixer/:id                          — session details + chunks
  PATCH /mixer/:id                         — update title/description (draft)
  DELETE /mixer/:id                        — delete session (draft only)
  POST /mixer/:id/chunks                   — add chunk
  PATCH /mixer/:id/chunks/:chunkId         — update chunk (draft)
  DELETE /mixer/:id/chunks/:chunkId        — delete chunk (draft)
  POST /mixer/:id/start                    — start session (dispatch chunks)
  POST /mixer/:id/cancel                   — cancel session
  POST /mixer/:id/chunks/:chunkId/approve  — approve chunk output
  POST /mixer/:id/chunks/:chunkId/reject   — reject chunk (agent reworks)
  GET  /mixer/:id/chunks/:chunkId/messages — chunk messages
  POST /mixer/:id/chunks/:chunkId/messages — send message
  POST /mixer/:id/assemble                 — passphrase → decrypt → assemble output
  GET  /mixer/:id/fragments                — list placeholders (no values)
  GET  /mixer/:id/audit                    — audit log

Agent-facing (X-API-Key):
  GET  /mixer/agent/my-chunks              — ready/active chunks
  GET  /mixer/agent/chunk/:chunkId         — chunk details
  GET  /mixer/agent/chunk/:chunkId/messages — chunk messages
  POST /mixer/agent/chunk/:chunkId/messages — send message
  POST /mixer/agent/chunk/:chunkId/complete — submit output
"""

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.database import get_db
from app.repositories.chat_repo import ChatRepository, get_chat_repo
from app.repositories.mixer_repo import MixerRepository, get_mixer_repo
from app.services.mixer_service import MixerService, get_mixer_service
from app.schemas.mixer import (
    AddMixerChunkRequest,
    AgentCompleteMixerChunkRequest,
    AssembleMixerRequest,
    CreateMixerSessionRequest,
    MixerChunkMessageRequest,
    RejectMixerChunkRequest,
    UpdateMixerChunkRequest,
    UpdateMixerSessionRequest,
)

from loguru import logger
router = APIRouter(prefix="/mixer", tags=["mixer"])


# ── Helpers ────────────────────────────────────────────────────────────

def _session_to_response(s: dict) -> dict:
    return {
        "id": str(s["id"]),
        "user_id": str(s["user_id"]),
        "user_name": s.get("user_name"),
        "title": s["title"],
        "description": s.get("description"),
        "status": s["status"],
        "fragment_count": s.get("fragment_count", 0),
        "chunk_count": s.get("chunk_count", 0),
        "completed_chunk_count": s.get("completed_chunk_count", 0),
        "fragment_ttl_hours": s["fragment_ttl_hours"],
        "created_at": str(s["created_at"]),
        "started_at": str(s["started_at"]) if s.get("started_at") else None,
        "completed_at": str(s["completed_at"]) if s.get("completed_at") else None,
        "cancelled_at": str(s["cancelled_at"]) if s.get("cancelled_at") else None,
    }


def _chunk_to_response(c: dict) -> dict:
    return {
        "id": str(c["id"]),
        "session_id": str(c["session_id"]),
        "agent_id": str(c["agent_id"]),
        "agent_name": c.get("agent_name"),
        "agent_handle": c.get("agent_handle"),
        "specialization": c.get("specialization"),
        "chunk_order": c["chunk_order"],
        "title": c["title"],
        "instructions": c.get("instructions"),
        "status": c["status"],
        "output_text": c.get("output_text"),
        "leak_detected": c["leak_detected"],
        "leak_details": c.get("leak_details"),
        "started_at": str(c["started_at"]) if c.get("started_at") else None,
        "completed_at": str(c["completed_at"]) if c.get("completed_at") else None,
        "created_at": str(c["created_at"]),
    }


async def _get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    chat_repo: ChatRepository = Depends(get_chat_repo),
) -> dict:
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    agent = await chat_repo.get_agent_by_api_key_hash(key_hash)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return agent


async def _verify_session_owner(
    session_id: str, user: CurrentUser,
    repo: MixerRepository,
) -> dict:
    session = await repo.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Mixer session not found")
    if str(session["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    return session


# ══════════════════════════════════════════════════════════════════════
# User-facing endpoints
# ══════════════════════════════════════════════════════════════════════


@router.post("", summary="Create a new mixer session")
async def create_session(
    body: CreateMixerSessionRequest,
    user: CurrentUser,
    svc: MixerService = Depends(get_mixer_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await svc.create_session(
            str(user.id), body.title, body.description,
            body.task_text, body.passphrase, body.fragment_ttl_hours,
        )
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", summary="List my mixer sessions")
async def list_sessions(
    user: CurrentUser,
    limit: int = Query(default=50, le=200),
    repo: MixerRepository = Depends(get_mixer_repo),
):
    rows = await repo.list_user_sessions(str(user.id), limit)
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "description": r.get("description"),
            "status": r["status"],
            "fragment_count": r["fragment_count"],
            "chunk_count": r["chunk_count"],
            "completed_chunk_count": r["completed_chunk_count"],
            "fragment_ttl_hours": r["fragment_ttl_hours"],
            "created_at": str(r["created_at"]),
            "started_at": str(r["started_at"]) if r.get("started_at") else None,
            "completed_at": str(r["completed_at"]) if r.get("completed_at") else None,
        }
        for r in rows
    ]


@router.get("/{session_id}", summary="Get mixer session details with chunks")
async def get_session(
    session_id: str,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
):
    session = await _verify_session_owner(session_id, user, repo)
    chunks = await repo.get_session_chunks(session_id)
    fragments = await repo.get_fragment_placeholders(session_id)

    resp = _session_to_response(session)
    resp["original_text"] = session.get("original_text")
    resp["chunks"] = [_chunk_to_response(c) for c in chunks]
    resp["fragments"] = fragments
    return resp


@router.patch("/{session_id}", summary="Update session (draft)")
async def update_session(
    session_id: str,
    body: UpdateMixerSessionRequest,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    session = await _verify_session_owner(session_id, user, repo)
    if session["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only update draft sessions")

    fields = body.model_dump(exclude_none=True)
    if not fields:
        return _session_to_response(session)

    await repo.update_session(session_id, **fields)
    await db.commit()
    updated = await repo.get_session_by_id(session_id)
    return _session_to_response(updated)


@router.delete("/{session_id}", summary="Delete session (draft only)")
async def delete_session(
    session_id: str,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_session_owner(session_id, user, repo)
    deleted = await repo.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Can only delete draft sessions")
    await db.commit()
    return {"ok": True}


# ── Chunks ──────────────────────────────────────────────────────────


@router.post("/{session_id}/chunks", summary="Add chunk to session")
async def add_chunk(
    session_id: str,
    body: AddMixerChunkRequest,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    session = await _verify_session_owner(session_id, user, repo)
    if session["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only add chunks to draft sessions")

    result = await repo.create_chunk(
        session_id, body.agent_id, body.title, body.instructions,
    )
    await db.commit()
    return {"id": str(result["id"]), "chunk_order": result["chunk_order"], "status": result["status"]}


@router.patch("/{session_id}/chunks/{chunk_id}", summary="Update chunk (draft)")
async def update_chunk(
    session_id: str,
    chunk_id: str,
    body: UpdateMixerChunkRequest,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    session = await _verify_session_owner(session_id, user, repo)
    if session["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only update chunks in draft sessions")

    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["session_id"]) != session_id:
        raise HTTPException(status_code=404, detail="Chunk not found")

    fields = body.model_dump(exclude_none=True)
    if fields:
        await repo.update_chunk(chunk_id, **fields)
        await db.commit()

    updated = await repo.get_chunk_by_id(chunk_id)
    return _chunk_to_response(updated)


@router.delete("/{session_id}/chunks/{chunk_id}", summary="Delete chunk (draft)")
async def delete_chunk(
    session_id: str,
    chunk_id: str,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_session_owner(session_id, user, repo)
    deleted = await repo.delete_chunk(chunk_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Can only delete chunks from draft sessions")
    await db.commit()
    return {"ok": True}


# ── Session Lifecycle ───────────────────────────────────────────────


@router.post("/{session_id}/start", summary="Start mixer session")
async def start_session(
    session_id: str,
    user: CurrentUser,
    svc: MixerService = Depends(get_mixer_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await svc.start_session(session_id, str(user.id))
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/cancel", summary="Cancel mixer session")
async def cancel_session(
    session_id: str,
    user: CurrentUser,
    svc: MixerService = Depends(get_mixer_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await svc.cancel_session(session_id, str(user.id))
        await db.commit()
        return _session_to_response(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Chunk Actions ───────────────────────────────────────────────────


@router.post("/{session_id}/chunks/{chunk_id}/approve", summary="Approve chunk output")
async def approve_chunk(
    session_id: str,
    chunk_id: str,
    user: CurrentUser,
    svc: MixerService = Depends(get_mixer_service),
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_session_owner(session_id, user, repo)
    try:
        result = await svc.approve_chunk(session_id, chunk_id, str(user.id))
        await db.commit()
        return _chunk_to_response(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/chunks/{chunk_id}/reject", summary="Reject chunk output")
async def reject_chunk(
    session_id: str,
    chunk_id: str,
    body: RejectMixerChunkRequest,
    user: CurrentUser,
    svc: MixerService = Depends(get_mixer_service),
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_session_owner(session_id, user, repo)
    try:
        result = await svc.reject_chunk(session_id, chunk_id, str(user.id), body.feedback)
        await db.commit()
        return _chunk_to_response(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Messages ────────────────────────────────────────────────────────


@router.get("/{session_id}/chunks/{chunk_id}/messages", summary="Get chunk messages")
async def get_chunk_messages(
    session_id: str,
    chunk_id: str,
    user: CurrentUser,
    limit: int = Query(default=50, le=500),
    before: str | None = Query(default=None),
    repo: MixerRepository = Depends(get_mixer_repo),
):
    await _verify_session_owner(session_id, user, repo)
    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["session_id"]) != session_id:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return await repo.get_messages(chunk_id, limit, before=before)


@router.post("/{session_id}/chunks/{chunk_id}/messages", summary="Send message in chunk chat")
async def send_chunk_message(
    session_id: str,
    chunk_id: str,
    body: MixerChunkMessageRequest,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_session_owner(session_id, user, repo)
    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["session_id"]) != session_id:
        raise HTTPException(status_code=404, detail="Chunk not found")

    msg = await repo.insert_message(chunk_id, "user", str(user.id), body.content, body.message_type)
    await db.commit()
    return msg


# ── Assembly ────────────────────────────────────────────────────────


@router.post("/{session_id}/assemble", summary="Decrypt and assemble output")
async def assemble(
    session_id: str,
    body: AssembleMixerRequest,
    user: CurrentUser,
    svc: MixerService = Depends(get_mixer_service),
    db: AsyncSession = Depends(get_db),
):
    try:
        output = await svc.assemble_output(session_id, body.passphrase, str(user.id))
        await db.commit()
        return {"assembled_output": output}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Fragments & Audit ───────────────────────────────────────────────


@router.get("/{session_id}/fragments", summary="List fragment placeholders (no values)")
async def list_fragments(
    session_id: str,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
):
    await _verify_session_owner(session_id, user, repo)
    return await repo.get_fragment_placeholders(session_id)


@router.get("/{session_id}/audit", summary="View audit log")
async def get_audit_log(
    session_id: str,
    user: CurrentUser,
    repo: MixerRepository = Depends(get_mixer_repo),
):
    await _verify_session_owner(session_id, user, repo)
    return await repo.get_audit_log(session_id)


# ══════════════════════════════════════════════════════════════════════
# Agent-facing endpoints
# ══════════════════════════════════════════════════════════════════════


@router.get("/agent/my-chunks", summary="Get ready/active chunks for this agent")
async def agent_my_chunks(
    agent: dict = Depends(_get_agent_by_api_key),
    repo: MixerRepository = Depends(get_mixer_repo),
):
    chunks = await repo.get_agent_ready_chunks(str(agent["id"]))
    return [
        {
            "chunk_id": str(c["id"]),
            "session_id": str(c["session_id"]),
            "session_title": c["session_title"],
            "title": c["title"],
            "instructions": c.get("instructions"),
            "status": c["status"],
        }
        for c in chunks
    ]


@router.get("/agent/chunk/{chunk_id}", summary="Get chunk details")
async def agent_get_chunk(
    chunk_id: str,
    agent: dict = Depends(_get_agent_by_api_key),
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Chunk not found or not assigned to you")

    # Mark as active if ready
    if chunk["status"] == "ready":
        await repo.update_chunk_status(chunk_id, "active")
        await db.commit()
        chunk = await repo.get_chunk_by_id(chunk_id)

    return _chunk_to_response(chunk)


@router.get("/agent/chunk/{chunk_id}/messages", summary="Get chunk messages (agent)")
async def agent_get_messages(
    chunk_id: str,
    limit: int = Query(default=50, le=500),
    before: str | None = Query(default=None),
    agent: dict = Depends(_get_agent_by_api_key),
    repo: MixerRepository = Depends(get_mixer_repo),
):
    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Chunk not found")
    return await repo.get_messages(chunk_id, limit, before=before)


@router.post("/agent/chunk/{chunk_id}/messages", summary="Send message (agent)")
async def agent_send_message(
    chunk_id: str,
    body: MixerChunkMessageRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Chunk not found")

    msg = await repo.insert_message(chunk_id, "agent", str(agent["id"]), body.content, body.message_type)
    await db.commit()
    return msg


@router.post("/agent/chunk/{chunk_id}/complete", summary="Submit chunk output (agent)")
async def agent_complete_chunk(
    chunk_id: str,
    body: AgentCompleteMixerChunkRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    svc: MixerService = Depends(get_mixer_service),
    repo: MixerRepository = Depends(get_mixer_repo),
    db: AsyncSession = Depends(get_db),
):
    chunk = await repo.get_chunk_by_id(chunk_id)
    if not chunk or str(chunk["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Chunk not found or not assigned to you")

    try:
        result = await svc.agent_complete_chunk(chunk_id, body.output_text)
        await db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
