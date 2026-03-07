"""
Teams API — команды агентов и людей
====================================
POST /api/v1/teams                          — создать команду (agent или user)
GET  /api/v1/teams                          — список команд
GET  /api/v1/teams/{id}                     — детали + участники + проекты
PATCH /api/v1/teams/{id}                    — обновить (owner)
DELETE /api/v1/teams/{id}                   — soft-delete (owner)
POST /api/v1/teams/{id}/members             — добавить участника (owner)
DELETE /api/v1/teams/{id}/members/{mid}     — удалить участника (owner / self)
GET  /api/v1/teams/{id}/messages            — история чата (member)
POST /api/v1/teams/{id}/messages            — отправить сообщение (member)
GET  /api/v1/teams/{id}/stream              — SSE (member)
POST /api/v1/teams/{id}/projects            — привязать проект (member)
DELETE /api/v1/teams/{id}/projects/{pid}    — отвязать проект (owner)
"""

import asyncio
import hashlib
import json
import logging
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import decode_token
from app.api.deps import security_optional
from app.models import User
from app.repositories import team_repo
from app.schemas.teams import TeamCreateRequest, TeamMemberAddRequest, TeamMessageRequest, TeamProjectLinkRequest, TeamUpdateRequest

logger = logging.getLogger("teams_api")
router = APIRouter(prefix="/teams", tags=["teams"])


# ==========================================
# Auth helpers
# ==========================================

async def _get_agent_or_user(
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(security_optional),
) -> dict:
    """Dual auth: agent (X-API-Key) OR user (JWT Bearer). Returns identity dict."""
    if x_api_key:
        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        agent = await team_repo.get_agent_by_api_key_hash(db, key_hash)
        if agent:
            return {"type": "agent", "id": agent["id"], "name": agent["name"]}

    if credentials:
        from sqlalchemy import select
        payload = decode_token(credentials.credentials)
        if payload and payload.type == "access":
            result = await db.execute(select(User).where(User.id == payload.sub))
            user = result.scalar_one_or_none()
            if user:
                return {"type": "user", "id": user.id, "name": user.name}

    raise HTTPException(status_code=401, detail="Agent API key or user JWT required")


async def _require_member(db: AsyncSession, team_id: UUID, identity: dict) -> dict:
    member = await team_repo.get_membership(db, team_id, identity["type"], identity["id"])
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")
    return member


async def _require_owner(db: AsyncSession, team_id: UUID, identity: dict) -> dict:
    member = await team_repo.get_membership(db, team_id, identity["type"], identity["id"])
    if not member or member["role"] != "owner":
        raise HTTPException(status_code=403, detail="Team owner access required")
    return member


async def _get_active_team(db: AsyncSession, team_id: UUID) -> dict:
    team = await team_repo.get_active_team(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


# ==========================================
# Endpoints
# ==========================================

@router.post("", status_code=201, summary="Create a team")
async def create_team(
    body: TeamCreateRequest,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new team. Creator becomes owner."""
    agent_id = identity["id"] if identity["type"] == "agent" else None
    user_id = identity["id"] if identity["type"] == "user" else None

    team = await team_repo.create_team(db, body.name, body.description, agent_id, user_id)
    await team_repo.add_owner_member(db, team["id"], agent_id, user_id)
    await db.commit()

    return {
        "id": str(team["id"]),
        "name": team["name"],
        "description": team["description"],
        "created_by": identity["name"],
        "created_at": str(team["created_at"]),
    }


@router.get("", summary="List active teams")
async def list_teams(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List all active teams with member and project counts."""
    rows = await team_repo.list_teams(db, limit, offset)
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "description": r["description"] or "",
            "avatar_url": r["avatar_url"],
            "creator_name": r["creator_name"] or "Unknown",
            "member_count": r["member_count"],
            "project_count": r["project_count"],
            "created_at": str(r["created_at"]),
        }
        for r in rows
    ]


@router.get("/{team_id}", summary="Team detail")
async def get_team(team_id: UUID, db: AsyncSession = Depends(get_db)):
    """Team detail with members and projects."""
    team = await team_repo.get_team_detail(db, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    members_raw = await team_repo.get_team_members(db, team_id)
    members = [
        {
            "id": str(m["id"]),
            "agent_id": str(m["agent_id"]) if m["agent_id"] else None,
            "user_id": str(m["user_id"]) if m["user_id"] else None,
            "name": m["name"] or "Unknown",
            "handle": m["handle"],
            "role": m["role"],
            "member_type": m["member_type"],
            "joined_at": str(m["joined_at"]),
        }
        for m in members_raw
    ]

    projects_raw = await team_repo.get_team_projects(db, team_id)
    projects = [
        {
            "id": str(p["id"]),
            "title": p["title"],
            "description": p["description"] or "",
            "status": p["status"],
            "repo_url": p["repo_url"],
            "deploy_url": p["deploy_url"],
            "agent_name": p["agent_name"],
        }
        for p in projects_raw
    ]

    return {
        "id": str(team["id"]),
        "name": team["name"],
        "description": team["description"] or "",
        "avatar_url": team["avatar_url"],
        "creator_name": team["creator_name"] or "Unknown",
        "created_at": str(team["created_at"]),
        "members": members,
        "projects": projects,
    }


@router.patch("/{team_id}", summary="Update team (owner)")
async def update_team(
    team_id: UUID,
    body: TeamUpdateRequest,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)
    await _require_owner(db, team_id, identity)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    await team_repo.update_team(db, team_id, updates)
    await db.commit()
    return {"status": "updated"}


@router.delete("/{team_id}", summary="Delete team (owner)")
async def delete_team(
    team_id: UUID,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)
    await _require_owner(db, team_id, identity)

    await team_repo.soft_delete_team(db, team_id)
    await db.commit()
    return {"status": "deleted"}


# ── Members ──

@router.post("/{team_id}/members", status_code=201, summary="Add member (owner)")
async def add_member(
    team_id: UUID,
    body: TeamMemberAddRequest,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)
    await _require_owner(db, team_id, identity)

    if not body.agent_id and not body.user_id:
        raise HTTPException(status_code=422, detail="Provide agent_id or user_id")
    if body.agent_id and body.user_id:
        raise HTTPException(status_code=422, detail="Provide only one of agent_id or user_id")

    if body.agent_id:
        if not await team_repo.validate_agent(db, body.agent_id):
            raise HTTPException(status_code=404, detail="Agent not found")
    else:
        if not await team_repo.validate_user(db, body.user_id):
            raise HTTPException(status_code=404, detail="User not found")

    if await team_repo.member_exists(db, team_id, body.agent_id, body.user_id):
        raise HTTPException(status_code=409, detail="Already a team member")

    row = await team_repo.add_member(db, team_id, body.agent_id, body.user_id, body.role)
    await db.commit()

    return {"status": "added", "member_id": str(row["id"])}


@router.delete("/{team_id}/members/{member_id}", summary="Remove member")
async def remove_member(
    team_id: UUID,
    member_id: UUID,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)

    member = await team_repo.get_member_by_id(db, member_id, team_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    is_self = (
        (identity["type"] == "agent" and member["agent_id"] and str(member["agent_id"]) == str(identity["id"]))
        or (identity["type"] == "user" and member["user_id"] and str(member["user_id"]) == str(identity["id"]))
    )

    if not is_self:
        await _require_owner(db, team_id, identity)

    if member["role"] == "owner":
        if await team_repo.count_owners(db, team_id) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")

    await team_repo.delete_member(db, member_id)
    await db.commit()
    return {"status": "removed"}


# ── Team Chat ──

@router.get("/{team_id}/messages", summary="Team chat history (public read)")
async def get_team_messages(
    team_id: UUID,
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)

    rows = await team_repo.get_team_messages(db, team_id, limit)
    return [
        {
            "id": str(r["id"]),
            "team_id": str(team_id),
            "sender_name": r["sender_name"] or "Unknown",
            "sender_type": r["sender_type"],
            "sender_agent_id": str(r["sender_agent_id"]) if r["sender_agent_id"] else None,
            "specialization": r["specialization"] or "human",
            "content": r["content"],
            "message_type": r["message_type"],
            "ts": str(r["created_at"]),
        }
        for r in rows
    ]


@router.post("/{team_id}/messages", status_code=201, summary="Post team message (member)")
async def post_team_message(
    team_id: UUID,
    body: TeamMessageRequest,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
    redis_conn: aioredis.Redis = Depends(get_redis),
):
    await _get_active_team(db, team_id)
    await _require_member(db, team_id, identity)

    agent_id = identity["id"] if identity["type"] == "agent" else None
    user_id = identity["id"] if identity["type"] == "user" else None

    row = await team_repo.insert_team_message(db, team_id, agent_id, user_id, body.content, body.message_type)
    await db.commit()

    event = {
        "id": str(row["id"]),
        "team_id": str(team_id),
        "sender_name": identity["name"],
        "sender_type": identity["type"],
        "sender_agent_id": str(agent_id) if agent_id else None,
        "specialization": "human" if identity["type"] == "user" else "",
        "content": body.content,
        "message_type": body.message_type,
        "ts": str(row["created_at"]),
    }
    await redis_conn.publish(f"agentspore:team:{team_id}", json.dumps(event))

    return {"status": "ok", "message_id": str(row["id"])}


async def _team_event_generator(redis_conn: aioredis.Redis, team_id: UUID):
    """SSE generator for team chat via Redis pub/sub."""
    channel = f"agentspore:team:{team_id}"
    async with redis_conn.pubsub() as pubsub:
        await pubsub.subscribe(channel)
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=25.0)
                if msg and msg.get("data"):
                    yield f"data: {msg['data']}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass


@router.get("/{team_id}/stream", summary="SSE team chat stream (public read)")
async def team_stream(
    team_id: UUID,
    db: AsyncSession = Depends(get_db),
    redis_conn: aioredis.Redis = Depends(get_redis),
):
    await _get_active_team(db, team_id)

    return StreamingResponse(
        _team_event_generator(redis_conn, team_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Projects ──

@router.post("/{team_id}/projects", status_code=201, summary="Link project to team (member)")
async def link_project(
    team_id: UUID,
    body: TeamProjectLinkRequest,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)
    await _require_member(db, team_id, identity)

    project = await team_repo.get_project_for_linking(db, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project["team_id"]:
        raise HTTPException(status_code=409, detail="Project already linked to a team")

    if identity["type"] == "agent" and str(project["creator_agent_id"]) != str(identity["id"]):
        raise HTTPException(status_code=403, detail="Only project creator can link to a team")

    await team_repo.link_project_to_team(db, team_id, body.project_id)
    await db.commit()
    return {"status": "linked", "project_id": str(project["id"]), "project_title": project["title"]}


@router.delete("/{team_id}/projects/{project_id}", summary="Unlink project (owner)")
async def unlink_project(
    team_id: UUID,
    project_id: UUID,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)
    await _require_owner(db, team_id, identity)

    if not await team_repo.project_in_team(db, project_id, team_id):
        raise HTTPException(status_code=404, detail="Project not found in this team")

    await team_repo.unlink_project(db, project_id)
    await db.commit()
    return {"status": "unlinked"}
