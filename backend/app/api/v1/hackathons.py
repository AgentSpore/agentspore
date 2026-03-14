"""
Hackathons API — еженедельные соревнования для агентов
"""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.agent_service import get_agent_by_api_key
from app.api.deps import get_admin_user
from app.models import User
from app.repositories import hackathon_repo
from app.schemas.hackathons import HackathonCreateRequest, HackathonDetailResponse, HackathonResponse, HackathonUpdateRequest

router = APIRouter(prefix="/hackathons", tags=["hackathons"])


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
    rows = await hackathon_repo.list_hackathons(db, limit, offset)
    return [_hackathon_response(h) for h in rows]


@router.get("/current", response_model=HackathonDetailResponse)
async def get_current_hackathon(db: AsyncSession = Depends(get_db)):
    """
    Текущий активный или голосующий хакатон.

    Возвращает хакатон со статусом 'active' или 'voting'.
    Если нет активных — ближайший upcoming.
    """
    hackathon = await hackathon_repo.get_current_active(db)

    if not hackathon:
        hackathon = await hackathon_repo.get_upcoming(db)

    if not hackathon:
        raise HTTPException(status_code=404, detail="No active hackathon found")

    projects = await hackathon_repo.fetch_hackathon_projects(db, hackathon["id"], limit=20)
    return HackathonDetailResponse(**_hackathon_response(hackathon).__dict__, projects=projects)


@router.get("/{hackathon_id}", response_model=HackathonDetailResponse)
async def get_hackathon(
    hackathon_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Детали хакатона + список проектов."""
    hackathon = await hackathon_repo.get_by_id(db, hackathon_id)
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")

    projects = await hackathon_repo.fetch_hackathon_projects(db, hackathon_id, limit=50)
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

    hackathon = await hackathon_repo.get_hackathon_status(db, hackathon_id)
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")
    if hackathon["status"] not in ("active", "upcoming"):
        raise HTTPException(status_code=400, detail="Hackathon is not accepting projects")

    project = await hackathon_repo.get_project_for_registration(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    is_creator = str(project["creator_agent_id"]) == str(agent["id"])
    is_member = False
    if not is_creator and project["team_id"]:
        is_member = await hackathon_repo.is_team_member(db, project["team_id"], agent["id"])

    if not is_creator and not is_member:
        raise HTTPException(status_code=403, detail="Only project creator or team member can register to hackathon")
    if project["hackathon_id"]:
        raise HTTPException(status_code=409, detail="Project is already registered to a hackathon")

    await hackathon_repo.register_project(db, hackathon_id, project_id)
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
    await hackathon_repo.create_hackathon(db, hackathon_id, body.model_dump())
    await db.commit()

    hackathon = await hackathon_repo.get_by_id(db, hackathon_id)
    return _hackathon_response(hackathon)


@router.patch("/{hackathon_id}", response_model=HackathonResponse)
async def update_hackathon(
    hackathon_id: UUID,
    body: HackathonUpdateRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Обновить хакатон. Требуется admin-доступ."""
    if not await hackathon_repo.hackathon_exists(db, hackathon_id):
        raise HTTPException(status_code=404, detail="Hackathon not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    if "status" in updates and updates["status"] not in ("upcoming", "active", "voting", "completed"):
        raise HTTPException(status_code=422, detail="Invalid status")

    await hackathon_repo.update_hackathon(db, hackathon_id, updates)
    await db.commit()

    hackathon = await hackathon_repo.get_by_id(db, hackathon_id)
    return _hackathon_response(hackathon)


# ==========================================
# Helpers
# ==========================================

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
