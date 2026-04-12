"""Councils API — create, read, stream messages, abort.

All mutating endpoints require an authenticated user. Read endpoints are
scoped to the caller: a user only sees their own councils. Public browsing
was intentionally removed in v1.1 — councils are now a personal/team tool,
not an open debate board (too easy to abuse OpenRouter free credits).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text as _sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.services.agent_service import get_agent_by_api_key
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.council_repo import CouncilRepository, get_council_repo
from app.services.council_service import CouncilService, get_council_service

# Per-user rate limit: N councils per rolling hour, enforced via Redis.
RATE_LIMIT_PER_HOUR = 10

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


class UserChatRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class AgentMessageRequest(BaseModel):
    """Used by platform agents (WS adapter) to post their reply for a round."""
    panelist_id: str
    round_num: int
    content: str = Field(..., min_length=1, max_length=8000)


# ── Endpoints ───────────────────────────────────────────────────────────


async def _check_rate_limit(user_id: str) -> None:
    """Sliding-window per-user rate limit for convening councils."""
    try:
        redis = await get_redis()
        key = f"council:ratelimit:{user_id}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 3600)
        if count > RATE_LIMIT_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_HOUR} councils per hour.",
            )
    except HTTPException:
        raise
    except Exception:
        # Redis outage must not block convening; log-and-continue is acceptable here.
        pass


async def _assert_owner(repo: CouncilRepository, council_id: str, user_id: str) -> dict:
    council = await repo.get_by_id(council_id)
    if not council:
        raise HTTPException(404, "council not found")
    if str(council.get("convener_user_id") or "") != str(user_id):
        raise HTTPException(403, "not your council")
    return council


@router.post("", summary="Convene a new council (auth required)")
async def create_council(
    body: ConveneRequest,
    request: Request,
    user: CurrentUser,
    svc: CouncilService = Depends(get_council_service),
):
    await _check_rate_limit(str(user.id))
    panelists = [p.model_dump() for p in body.panelists] if body.panelists else None
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
        convener_user_id=str(user.id),
        convener_ip=ip,
        is_public=False,
    )
    return {"id": str(council["id"]), "status": council["status"]}


@router.get("", summary="List my councils")
async def list_my_councils(
    user: CurrentUser,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        _sql_text("""
            SELECT id, topic, status, mode, panel_size, current_round, max_rounds,
                   consensus_score, created_at, ended_at
            FROM councils
            WHERE convener_user_id = CAST(:uid AS UUID)
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"uid": str(user.id), "limit": limit, "offset": offset},
    )).mappings().all()
    return [dict(r) for r in rows]


@router.get("/{council_id}")
async def get_council(
    council_id: str,
    user: CurrentUser,
    repo: CouncilRepository = Depends(get_council_repo),
):
    council = await _assert_owner(repo, council_id, str(user.id))
    panelists = await repo.list_panelists(council_id)
    votes = await repo.list_votes(council_id)
    return {"council": council, "panelists": panelists, "votes": votes}


@router.get("/{council_id}/messages")
async def list_messages(
    council_id: str,
    user: CurrentUser,
    repo: CouncilRepository = Depends(get_council_repo),
):
    await _assert_owner(repo, council_id, str(user.id))
    return await repo.list_messages(council_id)


@router.post("/{council_id}/chat", summary="Send a user message to the council")
async def user_chat(
    council_id: str,
    body: UserChatRequest,
    user: CurrentUser,
    svc: CouncilService = Depends(get_council_service),
    repo: CouncilRepository = Depends(get_council_repo),
):
    await _assert_owner(repo, council_id, str(user.id))
    try:
        round_num = await svc.handle_user_message(council_id, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "round": round_num}


@router.post("/{council_id}/finish", summary="Trigger vote + resolution")
async def finish_council(
    council_id: str,
    user: CurrentUser,
    svc: CouncilService = Depends(get_council_service),
    repo: CouncilRepository = Depends(get_council_repo),
):
    await _assert_owner(repo, council_id, str(user.id))
    try:
        await svc.finish_council(council_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "voting"}


@router.post("/{council_id}/abort", summary="Abort a running council")
async def abort_council(
    council_id: str,
    user: CurrentUser,
    repo: CouncilRepository = Depends(get_council_repo),
):
    council = await _assert_owner(repo, council_id, str(user.id))
    if council["status"] in ("done", "aborted"):
        return {"status": council["status"], "note": "already finished"}
    await repo.update_status(council_id, "aborted", ended=True)
    await repo.add_message(
        council_id, kind="system", content="[aborted by convener]",
    )
    await repo.db.commit()
    return {"status": "aborted"}


@router.get("/{council_id}/stream")
async def stream_messages(
    council_id: str,
    user: CurrentUser,
    repo: CouncilRepository = Depends(get_council_repo),
):
    """SSE stream of new messages + status transitions for a live council.

    Scoped to the council owner.
    """
    await _assert_owner(repo, council_id, str(user.id))

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
    agent: dict = Depends(get_agent_by_api_key),
    repo: CouncilRepository = Depends(get_council_repo),
):
    """Endpoint platform-WS panelists POST to when they answer a `council_turn` event.

    Authenticated via agent X-API-Key; the panelist row must reference that agent.
    """
    council = await repo.get_by_id(council_id)
    if not council:
        raise HTTPException(404, "council not found")
    if council["status"] not in ("round", "voting", "responding"):
        raise HTTPException(400, f"council not accepting messages in status '{council['status']}'")
    panelists = await repo.list_panelists(council_id)
    panelist = next((p for p in panelists if str(p["id"]) == body.panelist_id), None)
    if panelist is None:
        raise HTTPException(404, "panelist not found in this council")
    if str(panelist.get("agent_id") or "") != str(agent["id"]):
        raise HTTPException(403, "API key does not match this panelist")
    msg = await repo.add_message(
        council_id,
        kind="message",
        content=body.content,
        round_num=body.round_num,
        panelist_id=body.panelist_id,
    )
    await repo.db.commit()
    return {"id": str(msg["id"])}
