"""
Flows API — DAG-based multi-agent pipelines
=============================================
User-facing (Bearer JWT):
  POST /flows                         — create flow (draft)
  GET  /flows                         — list my flows
  GET  /flows/:id                     — flow details + steps
  PATCH /flows/:id                    — update title/description (draft)
  DELETE /flows/:id                   — delete flow (draft only)
  POST /flows/:id/steps               — add step
  PATCH /flows/:id/steps/:stepId      — update step (draft)
  DELETE /flows/:id/steps/:stepId     — delete step (draft)
  POST /flows/:id/start               — validate DAG & start
  POST /flows/:id/pause               — pause flow
  POST /flows/:id/resume              — resume flow
  POST /flows/:id/cancel              — cancel flow
  POST /flows/:id/steps/:stepId/approve  — approve step output
  POST /flows/:id/steps/:stepId/reject   — reject step (agent reworks)
  POST /flows/:id/steps/:stepId/skip     — skip step
  GET  /flows/:id/steps/:stepId/messages — step chat messages
  POST /flows/:id/steps/:stepId/messages — send message in step chat

Agent-facing (X-API-Key):
  GET  /flows/agent/my-steps              — ready/active steps for this agent
  GET  /flows/agent/step/:stepId          — step details
  GET  /flows/agent/step/:stepId/messages — step messages
  POST /flows/agent/step/:stepId/messages — send message
  POST /flows/agent/step/:stepId/complete — mark step done
"""

import hashlib
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.api.deps import CurrentUser
from app.repositories.chat_repo import ChatRepository, get_chat_repo
from app.repositories.flow_repo import FlowRepository, get_flow_repo
from app.services.flow_service import FlowService, get_flow_service
from app.schemas.flows import (
    AddStepRequest,
    AgentCompleteStepRequest,
    ApproveStepRequest,
    CreateFlowRequest,
    RejectStepRequest,
    SkipStepRequest,
    StepMessageRequest,
    UpdateFlowRequest,
    UpdateStepRequest,
)

from loguru import logger
router = APIRouter(prefix="/flows", tags=["flows"])

FLOW_CHANNEL = "agentspore:flow"


# ── Helpers ────────────────────────────────────────────────────────────

def _flow_to_response(f: dict) -> dict:
    return {
        "id": str(f["id"]),
        "user_id": str(f["user_id"]),
        "user_name": f.get("user_name"),
        "title": f["title"],
        "description": f.get("description"),
        "status": f["status"],
        "total_price_tokens": f["total_price_tokens"],
        "total_platform_fee": f["total_platform_fee"],
        "created_at": str(f["created_at"]),
        "started_at": str(f["started_at"]) if f.get("started_at") else None,
        "completed_at": str(f["completed_at"]) if f.get("completed_at") else None,
        "cancelled_at": str(f["cancelled_at"]) if f.get("cancelled_at") else None,
    }


def _step_to_response(s: dict) -> dict:
    return {
        "id": str(s["id"]),
        "flow_id": str(s["flow_id"]),
        "agent_id": str(s["agent_id"]),
        "agent_name": s.get("agent_name"),
        "agent_handle": s.get("agent_handle"),
        "specialization": s.get("specialization"),
        "step_order": s["step_order"],
        "title": s["title"],
        "instructions": s.get("instructions"),
        "depends_on": s.get("depends_on") or [],
        "status": s["status"],
        "auto_approve": s["auto_approve"],
        "input_text": s.get("input_text"),
        "output_text": s.get("output_text"),
        "output_files": s.get("output_files") or [],
        "price_tokens": s["price_tokens"],
        "platform_fee": s["platform_fee"],
        "started_at": str(s["started_at"]) if s.get("started_at") else None,
        "completed_at": str(s["completed_at"]) if s.get("completed_at") else None,
        "created_at": str(s["created_at"]),
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


async def _verify_flow_owner(
    flow_id: str, user: CurrentUser, repo: FlowRepository,
) -> dict:
    flow = await repo.get_flow_by_id(flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    if str(flow["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    return flow


# ══════════════════════════════════════════════════════════════════════
# User-facing endpoints
# ══════════════════════════════════════════════════════════════════════

@router.post("", summary="Create a new flow")
async def create_flow(
    body: CreateFlowRequest,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
):
    result = await repo.create_flow(user.id, body.title, body.description)
    return {"id": str(result["id"]), "status": result["status"], "created_at": str(result["created_at"])}


@router.get("", summary="List my flows")
async def list_flows(
    user: CurrentUser,
    limit: int = Query(default=50, le=200),
    repo: FlowRepository = Depends(get_flow_repo),
):
    rows = await repo.list_user_flows(user.id, limit)
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "description": r.get("description"),
            "status": r["status"],
            "total_price_tokens": r["total_price_tokens"],
            "total_platform_fee": r["total_platform_fee"],
            "step_count": r["step_count"],
            "completed_step_count": r["completed_step_count"],
            "created_at": str(r["created_at"]),
            "started_at": str(r["started_at"]) if r.get("started_at") else None,
            "completed_at": str(r["completed_at"]) if r.get("completed_at") else None,
        }
        for r in rows
    ]


@router.get("/{flow_id}", summary="Get flow details with steps")
async def get_flow(
    flow_id: str,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    steps = await repo.get_flow_steps(flow_id)
    return {
        **_flow_to_response(flow),
        "steps": [_step_to_response(s) for s in steps],
    }


@router.patch("/{flow_id}", summary="Update flow (draft only)")
async def update_flow(
    flow_id: str,
    body: UpdateFlowRequest,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    if flow["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only edit draft flows")

    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    await repo.update_flow(flow_id, **fields)
    return {"status": "ok"}


@router.delete("/{flow_id}", summary="Delete flow (draft only)")
async def delete_flow(
    flow_id: str,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    if flow["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only delete draft flows")
    deleted = await repo.delete_flow(flow_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Could not delete flow")
    return {"status": "deleted"}


# ── Steps CRUD ─────────────────────────────────────────────────────

@router.post("/{flow_id}/steps", summary="Add step to flow")
async def add_step(
    flow_id: str,
    body: AddStepRequest,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
    service: FlowService = Depends(get_flow_service),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    if flow["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only add steps to draft flows")

    try:
        await service.verify_agent_exists(body.agent_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    step = await repo.create_step(
        flow_id, body.agent_id, body.title,
        body.instructions, body.depends_on, body.auto_approve,
    )
    return {
        "id": str(step["id"]),
        "step_order": step["step_order"],
        "status": step["status"],
        "created_at": str(step["created_at"]),
    }


@router.patch("/{flow_id}/steps/{step_id}", summary="Update step (draft only)")
async def update_step(
    flow_id: str,
    step_id: str,
    body: UpdateStepRequest,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
    service: FlowService = Depends(get_flow_service),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    if flow["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only edit steps in draft flows")

    step = await repo.get_step_by_id(step_id)
    if not step or str(step["flow_id"]) != flow_id:
        raise HTTPException(status_code=404, detail="Step not found in this flow")

    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Verify agent if changing
    if "agent_id" in fields:
        try:
            await service.verify_agent_exists(fields["agent_id"])
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    await repo.update_step(step_id, **fields)
    return {"status": "ok"}


@router.delete("/{flow_id}/steps/{step_id}", summary="Delete step (draft only)")
async def delete_step(
    flow_id: str,
    step_id: str,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    if flow["status"] != "draft":
        raise HTTPException(status_code=400, detail="Can only delete steps in draft flows")

    deleted = await repo.delete_step(step_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Step not found")
    return {"status": "deleted"}


# ── Flow Control ───────────────────────────────────────────────────

@router.post("/{flow_id}/start", summary="Validate DAG and start flow")
async def start_flow(
    flow_id: str,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        flow = await service.start_flow(flow_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    event = {"type": "flow_started", "flow_id": flow_id, "user_name": user.name, "title": flow["title"]}
    await redis_client.publish(FLOW_CHANNEL, json.dumps(event))
    logger.info("Flow %s started by %s", flow_id, user.name)

    return _flow_to_response(flow)


@router.post("/{flow_id}/pause", summary="Pause flow")
async def pause_flow(
    flow_id: str,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        flow = await service.pause_flow(flow_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _flow_to_response(flow)


@router.post("/{flow_id}/resume", summary="Resume paused flow")
async def resume_flow(
    flow_id: str,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        flow = await service.resume_flow(flow_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    return _flow_to_response(flow)


@router.post("/{flow_id}/cancel", summary="Cancel flow")
async def cancel_flow(
    flow_id: str,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        flow = await service.cancel_flow(flow_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _flow_to_response(flow)


# ── Step Actions ───────────────────────────────────────────────────

@router.post("/{flow_id}/steps/{step_id}/approve", summary="Approve step output")
async def approve_step(
    flow_id: str,
    step_id: str,
    body: ApproveStepRequest,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        step = await service.approve_step(flow_id, step_id, body.edited_output)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    event = {"type": "step_approved", "flow_id": flow_id, "step_id": step_id}
    await redis_client.publish(f"{FLOW_CHANNEL}:{flow_id}", json.dumps(event))

    return _step_to_response(step)


@router.post("/{flow_id}/steps/{step_id}/reject", summary="Reject step — agent reworks")
async def reject_step(
    flow_id: str,
    step_id: str,
    body: RejectStepRequest,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        step = await service.reject_step(flow_id, step_id, body.feedback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    event = {"type": "step_rejected", "flow_id": flow_id, "step_id": step_id, "feedback": body.feedback}
    await redis_client.publish(f"{FLOW_CHANNEL}:{flow_id}", json.dumps(event))

    return _step_to_response(step)


@router.post("/{flow_id}/steps/{step_id}/skip", summary="Skip step")
async def skip_step(
    flow_id: str,
    step_id: str,
    body: SkipStepRequest,
    user: CurrentUser,
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
):
    await _verify_flow_owner(flow_id, user, repo)
    try:
        step = await service.skip_step(flow_id, step_id, body.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    return _step_to_response(step)


# ── Step Messages ──────────────────────────────────────────────────

@router.get("/{flow_id}/steps/{step_id}/messages", summary="Get step messages")
async def get_step_messages(
    flow_id: str,
    step_id: str,
    user: CurrentUser,
    limit: int = Query(default=200, le=500),
    repo: FlowRepository = Depends(get_flow_repo),
):
    await _verify_flow_owner(flow_id, user, repo)
    step = await repo.get_step_by_id(step_id)
    if not step or str(step["flow_id"]) != flow_id:
        raise HTTPException(status_code=404, detail="Step not found in this flow")
    return await repo.get_messages(step_id, limit)


@router.post("/{flow_id}/steps/{step_id}/messages", summary="Send message in step chat")
async def send_step_message(
    flow_id: str,
    step_id: str,
    body: StepMessageRequest,
    user: CurrentUser,
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    flow = await _verify_flow_owner(flow_id, user, repo)
    step = await repo.get_step_by_id(step_id)
    if not step or str(step["flow_id"]) != flow_id:
        raise HTTPException(status_code=404, detail="Step not found in this flow")
    if flow["status"] not in ("running", "paused"):
        raise HTTPException(status_code=400, detail="Flow is not active")

    msg = await repo.insert_message(
        step_id, "user", user.id, body.content, body.message_type,
        body.file_url, body.file_name,
    )
    await db.commit()

    event = {
        "type": "step_message", "flow_id": flow_id, "step_id": step_id,
        "message_id": str(msg["id"]), "sender_type": "user",
        "sender_name": user.name, "content": body.content,
    }
    await redis_client.publish(f"{FLOW_CHANNEL}:{flow_id}", json.dumps(event))

    return {"status": "ok", "message_id": str(msg["id"])}


# ══════════════════════════════════════════════════════════════════════
# Agent-facing endpoints (X-API-Key)
# ══════════════════════════════════════════════════════════════════════

@router.get("/agent/my-steps", summary="List agent's ready/active flow steps")
async def agent_list_steps(
    agent: dict = Depends(_get_agent_by_api_key),
    repo: FlowRepository = Depends(get_flow_repo),
):
    steps = await repo.get_agent_ready_steps(str(agent["id"]))
    return [
        {
            "id": str(s["id"]),
            "flow_id": str(s["flow_id"]),
            "flow_title": s["flow_title"],
            "title": s["title"],
            "instructions": s.get("instructions"),
            "input_text": s.get("input_text"),
            "status": s["status"],
            "step_order": s["step_order"],
        }
        for s in steps
    ]


@router.get("/agent/step/{step_id}", summary="Get step details (agent)")
async def agent_get_step(
    step_id: str,
    agent: dict = Depends(_get_agent_by_api_key),
    repo: FlowRepository = Depends(get_flow_repo),
):
    step = await repo.get_step_by_id(step_id)
    if not step or str(step["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Step not found")
    return _step_to_response(step)


@router.get("/agent/step/{step_id}/messages", summary="Agent gets step messages")
async def agent_get_step_messages(
    step_id: str,
    agent: dict = Depends(_get_agent_by_api_key),
    repo: FlowRepository = Depends(get_flow_repo),
):
    step = await repo.get_step_by_id(step_id)
    if not step or str(step["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Step not found")
    return await repo.get_messages(step_id)


@router.post("/agent/step/{step_id}/messages", summary="Agent sends message in step chat")
async def agent_send_step_message(
    step_id: str,
    body: StepMessageRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    step = await repo.get_step_by_id(step_id)
    if not step or str(step["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Step not found")
    if step["status"] not in ("ready", "active"):
        raise HTTPException(status_code=400, detail="Step is not active")

    # Mark step as active on first agent message
    if step["status"] == "ready":
        await repo.update_step_status(step_id, "active")

    msg = await repo.insert_message(
        step_id, "agent", agent["id"], body.content, body.message_type,
        body.file_url, body.file_name,
    )
    await db.commit()

    event = {
        "type": "step_message", "flow_id": str(step["flow_id"]), "step_id": step_id,
        "message_id": str(msg["id"]), "sender_type": "agent",
        "sender_name": agent["name"], "content": body.content,
    }
    await redis_client.publish(f"{FLOW_CHANNEL}:{step['flow_id']}", json.dumps(event))

    return {"status": "ok", "message_id": str(msg["id"])}


@router.post("/agent/step/{step_id}/complete", summary="Agent completes step")
async def agent_complete_step(
    step_id: str,
    body: AgentCompleteStepRequest,
    agent: dict = Depends(_get_agent_by_api_key),
    service: FlowService = Depends(get_flow_service),
    repo: FlowRepository = Depends(get_flow_repo),
    db: AsyncSession = Depends(get_db),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    step = await repo.get_step_by_id(step_id)
    if not step or str(step["agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=404, detail="Step not found")

    try:
        result = await service.agent_complete_step(step_id, body.output_text, body.output_files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()

    event = {
        "type": "step_completed", "flow_id": str(step["flow_id"]),
        "step_id": step_id, "new_status": result["status"],
    }
    await redis_client.publish(f"{FLOW_CHANNEL}:{step['flow_id']}", json.dumps(event))

    return _step_to_response(result)
