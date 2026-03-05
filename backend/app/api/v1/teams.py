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
from typing import Literal
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import decode_token
from app.api.deps import security_optional
from app.models import User

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
    # Try agent auth first
    if x_api_key:
        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        result = await db.execute(
            text("SELECT id, name, specialization FROM agents WHERE api_key_hash = :h AND is_active = TRUE"),
            {"h": key_hash},
        )
        agent = result.mappings().first()
        if agent:
            return {"type": "agent", "id": agent["id"], "name": agent["name"]}

    # Try user auth
    if credentials:
        from sqlalchemy import select
        payload = decode_token(credentials.credentials)
        if payload and payload.type == "access":
            result = await db.execute(select(User).where(User.id == payload.sub))
            user = result.scalar_one_or_none()
            if user:
                return {"type": "user", "id": user.id, "name": user.name}

    raise HTTPException(status_code=401, detail="Agent API key or user JWT required")


async def _verify_membership(db: AsyncSession, team_id: UUID, identity: dict) -> dict | None:
    """Check if identity is a team member. Returns member row or None."""
    if identity["type"] == "agent":
        result = await db.execute(
            text("SELECT id, role FROM team_members WHERE team_id = :tid AND agent_id = :aid"),
            {"tid": team_id, "aid": identity["id"]},
        )
    else:
        result = await db.execute(
            text("SELECT id, role FROM team_members WHERE team_id = :tid AND user_id = :uid"),
            {"tid": team_id, "uid": identity["id"]},
        )
    return result.mappings().first()


async def _require_member(db: AsyncSession, team_id: UUID, identity: dict) -> dict:
    member = await _verify_membership(db, team_id, identity)
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")
    return dict(member)


async def _require_owner(db: AsyncSession, team_id: UUID, identity: dict) -> dict:
    member = await _verify_membership(db, team_id, identity)
    if not member or member["role"] != "owner":
        raise HTTPException(status_code=403, detail="Team owner access required")
    return dict(member)


async def _get_active_team(db: AsyncSession, team_id: UUID) -> dict:
    result = await db.execute(
        text("SELECT id, name, is_active FROM agent_teams WHERE id = :id"),
        {"id": team_id},
    )
    team = result.mappings().first()
    if not team or not team["is_active"]:
        raise HTTPException(status_code=404, detail="Team not found")
    return dict(team)


# ==========================================
# Models
# ==========================================

class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(default="", max_length=2000)


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class TeamMemberAddRequest(BaseModel):
    agent_id: str | None = None
    user_id: str | None = None
    role: str = Field(default="member", pattern=r"^(owner|member)$")


class TeamMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    message_type: Literal["text", "idea", "question", "alert"] = "text"


class TeamProjectLinkRequest(BaseModel):
    project_id: str


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

    result = await db.execute(
        text("""
            INSERT INTO agent_teams (name, description, created_by_agent_id, created_by_user_id)
            VALUES (:name, :desc, :agent_id, :user_id)
            RETURNING id, name, description, created_at
        """),
        {"name": body.name, "desc": body.description, "agent_id": agent_id, "user_id": user_id},
    )
    team = result.mappings().first()

    # Auto-add creator as owner
    await db.execute(
        text("""
            INSERT INTO team_members (team_id, agent_id, user_id, role)
            VALUES (:tid, :aid, :uid, 'owner')
        """),
        {"tid": team["id"], "aid": agent_id, "uid": user_id},
    )
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
    result = await db.execute(
        text("""
            SELECT t.id, t.name, t.description, t.avatar_url, t.created_at,
                   t.created_by_agent_id, t.created_by_user_id,
                   COALESCE(a.name, u.name) as creator_name,
                   (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id = t.id) as member_count,
                   (SELECT COUNT(*) FROM projects p WHERE p.team_id = t.id) as project_count
            FROM agent_teams t
            LEFT JOIN agents a ON a.id = t.created_by_agent_id
            LEFT JOIN users u ON u.id = t.created_by_user_id
            WHERE t.is_active = TRUE
            ORDER BY t.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    )
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
        for r in result.mappings()
    ]


@router.get("/{team_id}", summary="Team detail")
async def get_team(team_id: UUID, db: AsyncSession = Depends(get_db)):
    """Team detail with members and projects."""
    result = await db.execute(
        text("""
            SELECT t.id, t.name, t.description, t.avatar_url, t.created_at,
                   t.created_by_agent_id, t.created_by_user_id,
                   COALESCE(a.name, u.name) as creator_name
            FROM agent_teams t
            LEFT JOIN agents a ON a.id = t.created_by_agent_id
            LEFT JOIN users u ON u.id = t.created_by_user_id
            WHERE t.id = :id AND t.is_active = TRUE
        """),
        {"id": team_id},
    )
    team = result.mappings().first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Members
    members_result = await db.execute(
        text("""
            SELECT tm.id, tm.agent_id, tm.user_id, tm.role, tm.joined_at,
                   COALESCE(a.name, u.name) as name,
                   a.handle as handle,
                   CASE WHEN tm.agent_id IS NOT NULL THEN 'agent' ELSE 'user' END as member_type
            FROM team_members tm
            LEFT JOIN agents a ON a.id = tm.agent_id
            LEFT JOIN users u ON u.id = tm.user_id
            WHERE tm.team_id = :tid
            ORDER BY tm.role DESC, tm.joined_at ASC
        """),
        {"tid": team_id},
    )
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
        for m in members_result.mappings()
    ]

    # Projects
    projects_result = await db.execute(
        text("""
            SELECT p.id, p.title, p.description, p.status, p.repo_url, p.deploy_url,
                   a.name as agent_name
            FROM projects p
            JOIN agents a ON a.id = p.creator_agent_id
            WHERE p.team_id = :tid
            ORDER BY p.created_at DESC
        """),
        {"tid": team_id},
    )
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
        for p in projects_result.mappings()
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

    set_parts = [f"{k} = :{k}" for k in updates]
    set_parts.append("updated_at = NOW()")
    updates["id"] = team_id

    await db.execute(
        text(f"UPDATE agent_teams SET {', '.join(set_parts)} WHERE id = :id"),
        updates,
    )
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

    # Unlink projects
    await db.execute(text("UPDATE projects SET team_id = NULL WHERE team_id = :tid"), {"tid": team_id})
    # Soft-delete
    await db.execute(
        text("UPDATE agent_teams SET is_active = FALSE, updated_at = NOW() WHERE id = :id"),
        {"id": team_id},
    )
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

    agent_id = body.agent_id
    user_id = body.user_id

    # Validate entity exists
    if agent_id:
        check = await db.execute(
            text("SELECT id, name FROM agents WHERE id = :id AND is_active = TRUE"),
            {"id": agent_id},
        )
        if not check.mappings().first():
            raise HTTPException(status_code=404, detail="Agent not found")
    else:
        check = await db.execute(text("SELECT id, name FROM users WHERE id = :id"), {"id": user_id})
        if not check.mappings().first():
            raise HTTPException(status_code=404, detail="User not found")

    # Check not already member
    existing = await db.execute(
        text("""
            SELECT id FROM team_members
            WHERE team_id = :tid AND (agent_id = :aid OR user_id = :uid)
        """),
        {"tid": team_id, "aid": agent_id, "uid": user_id},
    )
    if existing.mappings().first():
        raise HTTPException(status_code=409, detail="Already a team member")

    result = await db.execute(
        text("""
            INSERT INTO team_members (team_id, agent_id, user_id, role)
            VALUES (:tid, :aid, :uid, :role)
            RETURNING id, joined_at
        """),
        {"tid": team_id, "aid": agent_id, "uid": user_id, "role": body.role},
    )
    await db.commit()
    row = result.mappings().first()

    return {"status": "added", "member_id": str(row["id"])}


@router.delete("/{team_id}/members/{member_id}", summary="Remove member")
async def remove_member(
    team_id: UUID,
    member_id: UUID,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)

    # Get the member being removed
    result = await db.execute(
        text("SELECT id, agent_id, user_id, role FROM team_members WHERE id = :mid AND team_id = :tid"),
        {"mid": member_id, "tid": team_id},
    )
    member = result.mappings().first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Check: is this self-removal or owner removing someone?
    is_self = (
        (identity["type"] == "agent" and member["agent_id"] and str(member["agent_id"]) == str(identity["id"]))
        or (identity["type"] == "user" and member["user_id"] and str(member["user_id"]) == str(identity["id"]))
    )

    if not is_self:
        await _require_owner(db, team_id, identity)

    # Don't allow removing the last owner
    if member["role"] == "owner":
        owners = await db.execute(
            text("SELECT COUNT(*) as cnt FROM team_members WHERE team_id = :tid AND role = 'owner'"),
            {"tid": team_id},
        )
        if owners.mappings().first()["cnt"] <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")

    await db.execute(text("DELETE FROM team_members WHERE id = :mid"), {"mid": member_id})
    await db.commit()
    return {"status": "removed"}


# ── Team Chat ──

@router.get("/{team_id}/messages", summary="Team chat history (member)")
async def get_team_messages(
    team_id: UUID,
    limit: int = Query(default=100, le=500),
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_active_team(db, team_id)
    await _require_member(db, team_id, identity)

    result = await db.execute(
        text("""
            SELECT m.id, m.content, m.message_type, m.created_at,
                   m.sender_agent_id, m.sender_user_id, m.human_name,
                   COALESCE(a.name, u.name, m.human_name) as sender_name,
                   a.specialization,
                   CASE WHEN m.sender_agent_id IS NOT NULL THEN 'agent' ELSE 'user' END as sender_type
            FROM team_messages m
            LEFT JOIN agents a ON a.id = m.sender_agent_id
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.team_id = :tid
            ORDER BY m.created_at DESC
            LIMIT :limit
        """),
        {"tid": team_id, "limit": limit},
    )
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
        for r in result.mappings()
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

    result = await db.execute(
        text("""
            INSERT INTO team_messages (team_id, sender_agent_id, sender_user_id, content, message_type)
            VALUES (:tid, :aid, :uid, :content, :mtype)
            RETURNING id, created_at
        """),
        {"tid": team_id, "aid": agent_id, "uid": user_id, "content": body.content, "mtype": body.message_type},
    )
    await db.commit()
    row = result.mappings().first()

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


@router.get("/{team_id}/stream", summary="SSE team chat stream (member)")
async def team_stream(
    team_id: UUID,
    identity: dict = Depends(_get_agent_or_user),
    db: AsyncSession = Depends(get_db),
    redis_conn: aioredis.Redis = Depends(get_redis),
):
    await _get_active_team(db, team_id)
    await _require_member(db, team_id, identity)

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

    # Check project exists and belongs to the requesting agent
    result = await db.execute(
        text("SELECT id, title, creator_agent_id, team_id FROM projects WHERE id = :pid"),
        {"pid": body.project_id},
    )
    project = result.mappings().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project["team_id"]:
        raise HTTPException(status_code=409, detail="Project already linked to a team")

    # Only the creator agent can link their project
    if identity["type"] == "agent" and str(project["creator_agent_id"]) != str(identity["id"]):
        raise HTTPException(status_code=403, detail="Only project creator can link to a team")

    await db.execute(
        text("UPDATE projects SET team_id = :tid WHERE id = :pid"),
        {"tid": team_id, "pid": body.project_id},
    )
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

    result = await db.execute(
        text("SELECT id FROM projects WHERE id = :pid AND team_id = :tid"),
        {"pid": project_id, "tid": team_id},
    )
    if not result.mappings().first():
        raise HTTPException(status_code=404, detail="Project not found in this team")

    await db.execute(
        text("UPDATE projects SET team_id = NULL WHERE id = :pid"),
        {"pid": project_id},
    )
    await db.commit()
    return {"status": "unlinked"}
