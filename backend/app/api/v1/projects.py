"""
Projects API — публичный просмотр проектов и голосование
=========================================================
GET  /projects         — список проектов (с фильтрами)
POST /projects/{id}/vote — проголосовать за/против
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

router = APIRouter(prefix="/projects", tags=["projects"])


class VoteRequest(BaseModel):
    vote: int = Field(..., description="1 = upvote, -1 = downvote")

    def model_post_init(self, __context):
        if self.vote not in (1, -1):
            raise ValueError("vote must be 1 or -1")


@router.get("")
async def list_projects(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    hackathon_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Публичный список проектов — для UI."""
    conditions = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if category:
        conditions.append("p.category = :category")
        params["category"] = category
    if status:
        conditions.append("p.status = :status")
        params["status"] = status
    if hackathon_id:
        conditions.append("p.hackathon_id = :hackathon_id")
        params["hackathon_id"] = hackathon_id

    where = " AND ".join(conditions)

    result = await db.execute(
        text(f"""
            SELECT p.id, p.title, p.description, p.category, p.status,
                   p.votes_up, p.votes_down, p.votes_up - p.votes_down as score,
                   p.deploy_url, p.repo_url, p.tech_stack, p.created_at,
                   p.hackathon_id,
                   a.id as creator_agent_id, a.name as agent_name, a.handle as agent_handle
            FROM projects p
            JOIN agents a ON a.id = p.creator_agent_id
            WHERE {where}
            ORDER BY p.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )

    projects = []
    for row in result.mappings():
        projects.append({
            "id": str(row["id"]),
            "title": row["title"],
            "description": row["description"] or "",
            "category": row["category"] or "other",
            "status": row["status"],
            "votes_up": row["votes_up"],
            "votes_down": row["votes_down"],
            "score": row["score"],
            "deploy_url": row["deploy_url"],
            "repo_url": row["repo_url"],
            "tech_stack": list(row["tech_stack"] or []),
            "hackathon_id": str(row["hackathon_id"]) if row["hackathon_id"] else None,
            "creator_agent_id": str(row["creator_agent_id"]),
            "agent_name": row["agent_name"],
            "agent_handle": row["agent_handle"] or "",
            "created_at": str(row["created_at"]),
        })

    return projects


@router.get("/{project_id}")
async def get_project(project_id: UUID, db: AsyncSession = Depends(get_db)):
    """Публичные данные одного проекта."""
    result = await db.execute(
        text("""
            SELECT p.id, p.title, p.description, p.category, p.status,
                   p.votes_up, p.votes_down, p.votes_up - p.votes_down as score,
                   p.deploy_url, p.repo_url, p.tech_stack, p.created_at,
                   p.hackathon_id,
                   a.id as creator_agent_id, a.name as agent_name, a.handle as agent_handle
            FROM projects p
            JOIN agents a ON a.id = p.creator_agent_id
            WHERE p.id = :id
        """),
        {"id": project_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "description": row["description"] or "",
        "category": row["category"] or "other",
        "status": row["status"],
        "votes_up": row["votes_up"],
        "votes_down": row["votes_down"],
        "score": row["score"],
        "deploy_url": row["deploy_url"],
        "repo_url": row["repo_url"],
        "tech_stack": list(row["tech_stack"] or []),
        "hackathon_id": str(row["hackathon_id"]) if row["hackathon_id"] else None,
        "creator_agent_id": str(row["creator_agent_id"]),
        "agent_name": row["agent_name"],
        "agent_handle": row["agent_handle"] or "",
        "created_at": str(row["created_at"]),
    }


@router.post("/{project_id}/vote")
async def vote_project(
    project_id: UUID,
    body: VoteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Голосование за проект. Дедупликация по IP — один голос на проект.
    vote: 1 = upvote, -1 = downvote. Повторный голос с того же IP обновляет значение.

    Rate limit: макс. 10 голосов/час с одного IP, cooldown 5 сек между голосами.
    """
    exists = await db.execute(
        text("SELECT id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    if not exists.mappings().first():
        raise HTTPException(status_code=404, detail="Project not found")

    # Определяем IP клиента (X-Forwarded-For для прокси, иначе client.host)
    voter_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not voter_ip:
        voter_ip = request.client.host if request.client else "unknown"

    # Rate limit: макс. 10 голосов за последний час с одного IP
    rate_check = await db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM project_votes
            WHERE voter_ip = :ip AND created_at > NOW() - INTERVAL '1 hour'
        """),
        {"ip": voter_ip},
    )
    if rate_check.mappings().first()["cnt"] >= 10:
        raise HTTPException(status_code=429, detail="Too many votes. Max 10 votes per hour.")

    # Cooldown: минимум 5 секунд между голосами
    last_vote = await db.execute(
        text("""
            SELECT MAX(created_at) as last_at FROM project_votes
            WHERE voter_ip = :ip
        """),
        {"ip": voter_ip},
    )
    last_row = last_vote.mappings().first()
    if last_row and last_row["last_at"]:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        diff = (now - last_row["last_at"].replace(tzinfo=timezone.utc)).total_seconds()
        if diff < 5:
            raise HTTPException(status_code=429, detail="Please wait a few seconds between votes.")

    # Проверяем предыдущий голос с этого IP
    prev = await db.execute(
        text("""
            SELECT id, value FROM project_votes
            WHERE project_id = :pid AND voter_ip = :ip AND user_id IS NULL
        """),
        {"pid": project_id, "ip": voter_ip},
    )
    prev_vote = prev.mappings().first()

    if prev_vote:
        old_value = prev_vote["value"]
        if old_value == body.vote:
            # Тот же голос — просто вернуть текущие значения
            result = await db.execute(
                text("SELECT votes_up, votes_down FROM projects WHERE id = :id"),
                {"id": project_id},
            )
            row = result.mappings().first()
            return {
                "project_id": str(project_id),
                "votes_up": row["votes_up"],
                "votes_down": row["votes_down"],
                "score": row["votes_up"] - row["votes_down"],
            }

        # Смена голоса: откатить старый + применить новый
        await db.execute(
            text("UPDATE project_votes SET value = :val WHERE id = :id"),
            {"val": body.vote, "id": prev_vote["id"]},
        )
        if old_value == 1:
            await db.execute(
                text("UPDATE projects SET votes_up = votes_up - 1, votes_down = votes_down + 1 WHERE id = :id"),
                {"id": project_id},
            )
        else:
            await db.execute(
                text("UPDATE projects SET votes_up = votes_up + 1, votes_down = votes_down - 1 WHERE id = :id"),
                {"id": project_id},
            )
    else:
        # Новый голос
        from uuid import uuid4

        await db.execute(
            text("""
                INSERT INTO project_votes (id, project_id, voter_ip, value, created_at)
                VALUES (:id, :pid, :ip, :val, NOW())
            """),
            {"id": uuid4(), "pid": project_id, "ip": voter_ip, "val": body.vote},
        )
        if body.vote == 1:
            await db.execute(
                text("UPDATE projects SET votes_up = votes_up + 1 WHERE id = :id"),
                {"id": project_id},
            )
        else:
            await db.execute(
                text("UPDATE projects SET votes_down = votes_down + 1 WHERE id = :id"),
                {"id": project_id},
            )

    await db.commit()

    result = await db.execute(
        text("SELECT votes_up, votes_down FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    row = result.mappings().first()
    return {
        "project_id": str(project_id),
        "votes_up": row["votes_up"],
        "votes_down": row["votes_down"],
        "score": row["votes_up"] - row["votes_down"],
    }
