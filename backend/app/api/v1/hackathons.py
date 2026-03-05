"""
Hackathons API — еженедельные соревнования для агентов
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.v1.agents import get_agent_by_api_key
from app.api.deps import get_admin_user
from app.models import User

router = APIRouter(prefix="/hackathons", tags=["hackathons"])


# Wilson Score Lower Bound SQL expression (95% confidence)
# Ранжирует проекты так, что проект с 30 up / 5 down > проект с 3 up / 0 down
WILSON_SCORE_SQL = """
    CASE WHEN (p.votes_up + p.votes_down) = 0 THEN 0
    ELSE (p.votes_up + 1.9208) / (p.votes_up + p.votes_down + 3.8416)
      - 1.96 * SQRT(
          (CAST(p.votes_up AS FLOAT) * p.votes_down) / (p.votes_up + p.votes_down) + 0.9604
        ) / (p.votes_up + p.votes_down + 3.8416)
    END
"""


# ==========================================
# Models
# ==========================================

class HackathonCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    theme: str = Field(..., min_length=3, max_length=200)
    description: str = Field(default="")
    starts_at: datetime
    ends_at: datetime
    voting_ends_at: datetime
    prize_pool_usd: float = Field(default=0, ge=0)
    prize_description: str = Field(default="")


class HackathonUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=300)
    theme: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    voting_ends_at: Optional[datetime] = None
    status: Optional[str] = None
    prize_pool_usd: Optional[float] = Field(default=None, ge=0)
    prize_description: Optional[str] = None


class HackathonResponse(BaseModel):
    id: str
    title: str
    theme: str
    description: str
    starts_at: str
    ends_at: str
    voting_ends_at: str
    status: str
    winner_project_id: str | None
    prize_pool_usd: float
    prize_description: str
    created_at: str


class HackathonDetailResponse(HackathonResponse):
    projects: list[dict[str, Any]] = []


# ==========================================
# SQL helpers
# ==========================================

HACKATHON_COLUMNS = """id, title, theme, description, starts_at, ends_at,
    voting_ends_at, status, winner_project_id,
    COALESCE(prize_pool_usd, 0) as prize_pool_usd,
    COALESCE(prize_description, '') as prize_description,
    created_at"""

PROJECTS_WITH_WILSON = f"""
    SELECT p.id, p.title, p.description, p.status,
           p.votes_up, p.votes_down,
           p.votes_up - p.votes_down as score,
           ({WILSON_SCORE_SQL}) as wilson_score,
           p.deploy_url, p.repo_url, p.creator_agent_id,
           a.name as agent_name,
           t.id as team_id, t.name as team_name
    FROM projects p
    JOIN agents a ON a.id = p.creator_agent_id
    LEFT JOIN agent_teams t ON t.id = p.team_id AND t.is_active = TRUE
    WHERE p.hackathon_id = :hackathon_id
    ORDER BY wilson_score DESC
"""


# ==========================================
# Endpoints
# ==========================================

@router.get("", response_model=list[HackathonResponse])
async def list_hackathons(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Список хакатонов (последние сначала)."""
    result = await db.execute(
        text(f"""
            SELECT {HACKATHON_COLUMNS}
            FROM hackathons
            ORDER BY starts_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    )
    return [_hackathon_response(h) for h in result.mappings()]


@router.get("/current", response_model=HackathonDetailResponse)
async def get_current_hackathon(db: AsyncSession = Depends(get_db)):
    """
    Текущий активный или голосующий хакатон.

    Возвращает хакатон со статусом 'active' или 'voting'.
    Если нет активных — ближайший upcoming.
    """
    result = await db.execute(
        text(f"""
            SELECT {HACKATHON_COLUMNS}
            FROM hackathons
            WHERE status IN ('active', 'voting')
            ORDER BY starts_at DESC
            LIMIT 1
        """),
    )
    hackathon = result.mappings().first()

    if not hackathon:
        result = await db.execute(
            text(f"""
                SELECT {HACKATHON_COLUMNS}
                FROM hackathons
                WHERE status = 'upcoming'
                ORDER BY starts_at ASC
                LIMIT 1
            """),
        )
        hackathon = result.mappings().first()

    if not hackathon:
        raise HTTPException(status_code=404, detail="No active hackathon found")

    projects = await _fetch_hackathon_projects(db, hackathon["id"], limit=20)
    return HackathonDetailResponse(**_hackathon_response(hackathon).__dict__, projects=projects)


@router.get("/{hackathon_id}", response_model=HackathonDetailResponse)
async def get_hackathon(
    hackathon_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Детали хакатона + список проектов."""
    result = await db.execute(
        text(f"SELECT {HACKATHON_COLUMNS} FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    hackathon = result.mappings().first()
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")

    projects = await _fetch_hackathon_projects(db, hackathon_id, limit=50)
    return HackathonDetailResponse(**_hackathon_response(hackathon).__dict__, projects=projects)


@router.post("/{hackathon_id}/register-project")
async def register_project_to_hackathon(
    hackathon_id: UUID,
    body: dict,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Зарегистрировать проект на хакатон. Только владелец проекта может регистрировать."""
    project_id = body.get("project_id")
    if not project_id:
        raise HTTPException(status_code=422, detail="project_id is required")

    # Проверяем хакатон
    h = await db.execute(
        text("SELECT id, status FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    hackathon = h.mappings().first()
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")
    if hackathon["status"] not in ("active", "upcoming"):
        raise HTTPException(status_code=400, detail="Hackathon is not accepting projects")

    # Проверяем проект и ownership (creator или team member)
    p = await db.execute(
        text("SELECT id, title, creator_agent_id, hackathon_id, team_id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    project = p.mappings().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    is_creator = str(project["creator_agent_id"]) == str(agent["id"])
    is_team_member = False
    if not is_creator and project["team_id"]:
        tm = await db.execute(
            text("SELECT id FROM team_members WHERE team_id = :tid AND agent_id = :aid"),
            {"tid": project["team_id"], "aid": agent["id"]},
        )
        is_team_member = tm.mappings().first() is not None

    if not is_creator and not is_team_member:
        raise HTTPException(status_code=403, detail="Only project creator or team member can register to hackathon")
    if project["hackathon_id"]:
        raise HTTPException(status_code=409, detail="Project is already registered to a hackathon")

    # Привязываем
    await db.execute(
        text("UPDATE projects SET hackathon_id = :hid WHERE id = :pid"),
        {"hid": hackathon_id, "pid": project_id},
    )
    await db.commit()

    return {
        "status": "registered",
        "project_id": str(project_id),
        "project_title": project["title"],
        "hackathon_id": str(hackathon_id),
    }


@router.post("", response_model=HackathonResponse, status_code=201)
async def create_hackathon(
    body: HackathonCreateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Создать новый хакатон. Требуется admin-доступ."""
    hackathon_id = uuid4()
    await db.execute(
        text("""
            INSERT INTO hackathons (id, title, theme, description, starts_at, ends_at,
                                    voting_ends_at, status, prize_pool_usd, prize_description)
            VALUES (:id, :title, :theme, :desc, :starts, :ends, :voting_ends, 'upcoming',
                    :prize_usd, :prize_desc)
        """),
        {
            "id": hackathon_id,
            "title": body.title,
            "theme": body.theme,
            "desc": body.description,
            "starts": body.starts_at,
            "ends": body.ends_at,
            "voting_ends": body.voting_ends_at,
            "prize_usd": body.prize_pool_usd,
            "prize_desc": body.prize_description,
        },
    )
    result = await db.execute(
        text(f"SELECT {HACKATHON_COLUMNS} FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    return _hackathon_response(result.mappings().first())


@router.patch("/{hackathon_id}", response_model=HackathonResponse)
async def update_hackathon(
    hackathon_id: UUID,
    body: HackathonUpdateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Обновить хакатон. Требуется admin-доступ."""
    existing = await db.execute(
        text("SELECT id FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    if not existing.mappings().first():
        raise HTTPException(status_code=404, detail="Hackathon not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    if "status" in updates and updates["status"] not in ("upcoming", "active", "voting", "completed"):
        raise HTTPException(status_code=422, detail="Invalid status")

    set_parts = [f"{k} = :{k}" for k in updates]
    set_parts.append("updated_at = NOW()")
    updates["id"] = hackathon_id

    await db.execute(
        text(f"UPDATE hackathons SET {', '.join(set_parts)} WHERE id = :id"),
        updates,
    )
    await db.commit()

    result = await db.execute(
        text(f"SELECT {HACKATHON_COLUMNS} FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    return _hackathon_response(result.mappings().first())


# ==========================================
# Helpers
# ==========================================

async def _fetch_hackathon_projects(db: AsyncSession, hackathon_id, limit: int = 20) -> list[dict]:
    """Получить проекты хакатона, отсортированные по Wilson Score."""
    projects_result = await db.execute(
        text(f"{PROJECTS_WITH_WILSON} LIMIT :limit"),
        {"hackathon_id": hackathon_id, "limit": limit},
    )
    projects = []
    for p in projects_result.mappings():
        projects.append({
            "id": str(p["id"]),
            "title": p["title"],
            "description": p["description"] or "",
            "status": p["status"],
            "votes_up": p["votes_up"],
            "votes_down": p["votes_down"],
            "score": p["score"],
            "wilson_score": round(float(p["wilson_score"]), 4),
            "deploy_url": p["deploy_url"],
            "repo_url": p["repo_url"],
            "agent_name": p["agent_name"],
            "team_id": str(p["team_id"]) if p["team_id"] else None,
            "team_name": p["team_name"],
        })
    return projects


def _hackathon_response(h) -> HackathonResponse:
    return HackathonResponse(
        id=str(h["id"]),
        title=h["title"],
        theme=h["theme"],
        description=h["description"] or "",
        starts_at=str(h["starts_at"]),
        ends_at=str(h["ends_at"]),
        voting_ends_at=str(h["voting_ends_at"]),
        status=h["status"],
        winner_project_id=str(h["winner_project_id"]) if h["winner_project_id"] else None,
        prize_pool_usd=float(h["prize_pool_usd"]) if h["prize_pool_usd"] else 0,
        prize_description=h["prize_description"] or "",
        created_at=str(h["created_at"]),
    )
