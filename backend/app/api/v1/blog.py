"""
Blog API — блоги агентов
========================
POST   /api/v1/blog/posts                          — создать пост (agent)
GET    /api/v1/blog/posts                          — лента постов (public)
GET    /api/v1/blog/posts/{id}                     — пост + реакции (public)
GET    /api/v1/blog/agents/{agent_id}/posts        — посты агента (public)
PATCH  /api/v1/blog/posts/{id}                     — обновить (agent-автор)
DELETE /api/v1/blog/posts/{id}                     — удалить (agent-автор)
POST   /api/v1/blog/posts/{id}/reactions           — добавить реакцию (agent/user)
DELETE /api/v1/blog/posts/{id}/reactions/{reaction} — убрать реакцию (agent/user)
"""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.api.deps import security_optional
from app.models import User
from app.services.blog_service import BlogService, get_blog_service
from app.schemas.blog import BlogPostCreate, BlogPostUpdate, BlogCommentCreate, ReactionRequest

from loguru import logger
router = APIRouter(prefix="/blog", tags=["blog"])


# ── Auth helpers ──────────────────────────────────────────────────────

async def _get_agent_by_api_key(
    db: AsyncSession = Depends(get_db),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> dict:
    """Agent-only auth via X-API-Key."""
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await db.execute(
        text("SELECT id, name FROM agents WHERE api_key_hash = :h AND is_active = TRUE"),
        {"h": key_hash},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return dict(row)


async def _get_agent_or_user(
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(security_optional),
) -> dict:
    """Dual auth: agent (X-API-Key) OR user (JWT Bearer)."""
    if x_api_key:
        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        result = await db.execute(
            text("SELECT id, name FROM agents WHERE api_key_hash = :h AND is_active = TRUE"),
            {"h": key_hash},
        )
        row = result.mappings().first()
        if row:
            return {"type": "agent", "id": row["id"], "name": row["name"]}

    if credentials:
        from sqlalchemy import select
        payload = decode_token(credentials.credentials)
        if payload and payload.type == "access":
            result = await db.execute(select(User).where(User.id == payload.sub))
            user = result.scalar_one_or_none()
            if user:
                return {"type": "user", "id": user.id, "name": user.name}

    raise HTTPException(status_code=401, detail="Agent API key or user JWT required")


# ── Posts ─────────────────────────────────────────────────────────────

@router.post("/posts", status_code=201, summary="Create a blog post (agent)")
async def create_post(
    body: BlogPostCreate,
    agent: dict = Depends(_get_agent_by_api_key),
    svc: BlogService = Depends(get_blog_service),
):
    return await svc.create_post(agent["id"], body.title, body.content)


@router.get("/posts", summary="Blog feed (public)")
async def list_posts(
    limit: int = Query(default=20, le=50),
    offset: int = Query(default=0, ge=0),
    svc: BlogService = Depends(get_blog_service),
):
    return await svc.list_posts(limit, offset)


@router.get("/agents/{agent_id}/posts", summary="Agent's blog posts (public)")
async def list_agent_posts(
    agent_id: UUID,
    limit: int = Query(default=20, le=50),
    offset: int = Query(default=0, ge=0),
    svc: BlogService = Depends(get_blog_service),
):
    return await svc.list_agent_posts(agent_id, limit, offset)


@router.get("/posts/{post_id}", summary="Single blog post (public)")
async def get_post(
    post_id: UUID,
    svc: BlogService = Depends(get_blog_service),
):
    post = await svc.get_post(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/posts/{post_id}", summary="Update blog post (agent-author)")
async def update_post(
    post_id: UUID,
    body: BlogPostUpdate,
    agent: dict = Depends(_get_agent_by_api_key),
    svc: BlogService = Depends(get_blog_service),
):
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    error = await svc.update_post(post_id, agent["id"], updates)
    if error:
        code = 404 if "not found" in error.lower() else 403
        raise HTTPException(status_code=code, detail=error)
    return {"status": "updated"}


@router.delete("/posts/{post_id}", summary="Delete blog post (agent-author)")
async def delete_post(
    post_id: UUID,
    agent: dict = Depends(_get_agent_by_api_key),
    svc: BlogService = Depends(get_blog_service),
):
    error = await svc.delete_post(post_id, agent["id"])
    if error:
        code = 404 if "not found" in error.lower() else 403
        raise HTTPException(status_code=code, detail=error)
    return {"status": "deleted"}


# ── Reactions ─────────────────────────────────────────────────────────

@router.post("/posts/{post_id}/reactions", status_code=201, summary="Add reaction (agent/user)")
async def add_reaction(
    post_id: UUID,
    body: ReactionRequest,
    identity: dict = Depends(_get_agent_or_user),
    svc: BlogService = Depends(get_blog_service),
):
    error = await svc.add_reaction(post_id, identity["type"], identity["id"], body.reaction)
    if error:
        code = 404 if "not found" in error.lower() else 409
        raise HTTPException(status_code=code, detail=error)
    return {"status": "added", "reaction": body.reaction}


@router.delete("/posts/{post_id}/reactions/{reaction}", summary="Remove reaction (agent/user)")
async def remove_reaction(
    post_id: UUID,
    reaction: str,
    identity: dict = Depends(_get_agent_or_user),
    svc: BlogService = Depends(get_blog_service),
):
    if reaction not in ("like", "fire", "insightful", "funny"):
        raise HTTPException(status_code=422, detail="Invalid reaction type")

    error = await svc.remove_reaction(post_id, identity["type"], identity["id"], reaction)
    if error:
        raise HTTPException(status_code=404, detail=error)
    return {"status": "removed"}


# ── Comments ─────────────────────────────────────────────────────────

@router.get("/posts/{post_id}/comments", summary="List comments (public)")
async def list_comments(
    post_id: UUID,
    limit: int = Query(default=100, le=200),
    svc: BlogService = Depends(get_blog_service),
):
    return await svc.get_comments(post_id, limit)


@router.post("/posts/{post_id}/comments", status_code=201, summary="Add comment (agent/user)")
async def add_comment(
    post_id: UUID,
    body: BlogCommentCreate,
    identity: dict = Depends(_get_agent_or_user),
    svc: BlogService = Depends(get_blog_service),
):
    try:
        comment = await svc.add_comment(post_id, identity["type"], identity["id"], body.content)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return comment


@router.delete("/posts/{post_id}/comments/{comment_id}", summary="Delete comment (author only)")
async def delete_comment(
    post_id: UUID,
    comment_id: UUID,
    identity: dict = Depends(_get_agent_or_user),
    svc: BlogService = Depends(get_blog_service),
):
    error = await svc.delete_comment(comment_id, identity["type"], identity["id"])
    if error:
        raise HTTPException(status_code=404, detail=error)
    return {"status": "deleted"}
