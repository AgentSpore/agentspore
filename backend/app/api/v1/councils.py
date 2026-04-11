"""Councils API — create, read, stream messages, post (for platform agents)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.council_repo import CouncilRepository, get_council_repo
from app.services.council_service import CouncilService, get_council_service

router = APIRouter(prefix="/councils", tags=["councils"])


# ── Schemas ─────────────────────────────────────────────────────────────


class PanelistRef(BaseModel):
    adapter: str = Field(..., description="pure_llm | platform_ws")
    model_id: str | None = None
    agent_id: str | None = None
    display_name: str | None = None
    role: str = "panelist"
    perspective: str | None = None


class ConveneRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=300)
    brief: str = Field(..., min_length=10, max_length=5000)
    mode: str = "round_robin"
    panel_size: int = 5
    max_rounds: int = 3
    max_tokens_per_msg: int = 500
    timebox_seconds: int = 600
    panelists: list[PanelistRef] | None = None
    is_public: bool = True


class AgentMessageRequest(BaseModel):
    """Used by platform agents (WS adapter) to post their reply for a round."""
    panelist_id: str
    round_num: int
    content: str = Field(..., min_length=1, max_length=8000)


# ── Endpoints ───────────────────────────────────────────────────────────


@router.post("", summary="Convene a new council")
async def create_council(
    body: ConveneRequest,
    request: Request,
    svc: CouncilService = Depends(get_council_service),
):
    panelists = [p.model_dump() for p in body.panelists] if body.panelists else None
    # Pull client IP for anon public councils (rate limiting hook).
    ip = request.client.host if request.client else None
    council = await svc.convene(
        topic=body.topic,
        brief=body.brief,
        mode=body.mode,
        panel_size=body.panel_size,
        max_rounds=body.max_rounds,
        max_tokens_per_msg=body.max_tokens_per_msg,
        timebox_seconds=body.timebox_seconds,
        panelists=panelists,
        convener_ip=ip,
        is_public=body.is_public,
    )
    return {"id": str(council["id"]), "status": council["status"]}


@router.get("", summary="List public councils")
async def list_councils(
    limit: int = 20,
    offset: int = 0,
    repo: CouncilRepository = Depends(get_council_repo),
):
    return await repo.list_public(limit=limit, offset=offset)


@router.get("/{council_id}")
async def get_council(
    council_id: str,
    repo: CouncilRepository = Depends(get_council_repo),
):
    council = await repo.get_by_id(council_id)
    if not council:
        raise HTTPException(404, "council not found")
    panelists = await repo.list_panelists(council_id)
    votes = await repo.list_votes(council_id)
    return {"council": council, "panelists": panelists, "votes": votes}


@router.get("/{council_id}/messages")
async def list_messages(
    council_id: str,
    repo: CouncilRepository = Depends(get_council_repo),
):
    return await repo.list_messages(council_id)


@router.get("/{council_id}/stream")
async def stream_messages(council_id: str):
    """SSE stream of new messages + status transitions for a live council."""

    async def event_gen():
        last_id: str | None = None
        last_status: str | None = None
        from app.core.database import async_session_maker

        while True:
            async with async_session_maker() as session:
                repo = CouncilRepository(session)
                council = await repo.get_by_id(council_id)
                if not council:
                    yield f"event: error\ndata: {json.dumps({'error': 'not found'})}\n\n"
                    return
                msgs = await repo.list_messages(council_id, after_id=last_id)

            if council["status"] != last_status:
                yield f"event: status\ndata: {json.dumps({'status': council['status'], 'round': council['current_round']})}\n\n"
                last_status = council["status"]

            for m in msgs:
                last_id = str(m["id"])
                payload = {
                    "id": last_id,
                    "kind": m["kind"],
                    "round_num": m["round_num"],
                    "panelist_id": str(m.get("panelist_id") or ""),
                    "content": m["content"],
                    "created_at": str(m["created_at"]),
                }
                yield f"event: message\ndata: {json.dumps(payload)}\n\n"

            if council["status"] in ("done", "aborted"):
                yield f"event: end\ndata: {json.dumps({'status': council['status']})}\n\n"
                return

            await asyncio.sleep(1.0)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/{council_id}/messages")
async def post_agent_message(
    council_id: str,
    body: AgentMessageRequest,
    repo: CouncilRepository = Depends(get_council_repo),
):
    """Endpoint platform-WS panelists POST to when they answer a `council_turn` event."""
    council = await repo.get_by_id(council_id)
    if not council:
        raise HTTPException(404, "council not found")
    if council["status"] not in ("round", "voting"):
        raise HTTPException(400, f"council not accepting messages in status '{council['status']}'")
    msg = await repo.add_message(
        council_id,
        kind="message",
        content=body.content,
        round_num=body.round_num,
        panelist_id=body.panelist_id,
    )
    await repo.db.commit()
    return {"id": str(msg["id"])}
