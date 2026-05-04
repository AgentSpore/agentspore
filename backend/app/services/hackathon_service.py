"""HackathonService — business logic for hackathon management."""

from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import hackathon_repo
from app.schemas.hackathons import (
    HackathonCreateRequest,
    HackathonDetailResponse,
    HackathonResponse,
    HackathonUpdateRequest,
    CurrentHackathonResponse,
    RegisterProjectRequest,
)

VALID_STATUSES = ("upcoming", "active", "voting", "completed")


def _to_response(h: dict) -> HackathonResponse:
    """Map a raw hackathons row dict to HackathonResponse."""
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
        min_projects_to_start=h["min_projects_to_start"],
        duration_days=h["duration_days"],
    )


class HackathonService:
    """Business logic for hackathon lifecycle and project registration."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_all(self, limit: int, offset: int) -> list[HackathonResponse]:
        """Return paginated hackathons, newest first."""
        rows = await hackathon_repo.list_hackathons(self.db, limit, offset)
        return [_to_response(h) for h in rows]

    async def get_current(self) -> CurrentHackathonResponse:
        """Return the current active/voting hackathon, or the nearest upcoming one.

        Returns CurrentHackathonResponse with active=False when nothing is found.
        """
        hackathon = await hackathon_repo.get_current_active(self.db)
        if not hackathon:
            hackathon = await hackathon_repo.get_upcoming(self.db)
        if not hackathon:
            return CurrentHackathonResponse(active=False, hackathon=None)

        projects = await hackathon_repo.fetch_hackathon_projects(self.db, hackathon["id"], limit=20)
        detail = HackathonDetailResponse(**_to_response(hackathon).__dict__, projects=projects)
        return CurrentHackathonResponse(active=True, hackathon=detail)

    async def get_by_id(self, hackathon_id: UUID) -> HackathonDetailResponse:
        """Return hackathon with projects. Raises 404 if not found."""
        hackathon = await hackathon_repo.get_by_id(self.db, hackathon_id)
        if not hackathon:
            raise HTTPException(status_code=404, detail="Hackathon not found")

        projects = await hackathon_repo.fetch_hackathon_projects(self.db, hackathon_id, limit=50)
        return HackathonDetailResponse(**_to_response(hackathon).__dict__, projects=projects)

    async def register_project(
        self,
        hackathon_id: UUID,
        body: RegisterProjectRequest,
        agent: dict,
    ) -> dict:
        """Register a project to a hackathon.

        Validates hackathon accepts projects, checks ownership/membership,
        prevents double-registration, and triggers auto-start if threshold reached.
        """
        hackathon = await hackathon_repo.get_hackathon_status(self.db, hackathon_id)
        if not hackathon:
            raise HTTPException(status_code=404, detail="Hackathon not found")
        if hackathon["status"] not in ("active", "upcoming"):
            raise HTTPException(status_code=400, detail="Hackathon is not accepting projects")

        project = await hackathon_repo.get_project_for_registration(self.db, body.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        is_creator = str(project["creator_agent_id"]) == str(agent["id"])
        is_member = False
        if not is_creator and project["team_id"]:
            is_member = await hackathon_repo.is_team_member(self.db, project["team_id"], agent["id"])

        if not is_creator and not is_member:
            raise HTTPException(
                status_code=403,
                detail="Only project creator or team member can register to hackathon",
            )
        if project["hackathon_id"]:
            raise HTTPException(status_code=409, detail="Project is already registered to a hackathon")

        await hackathon_repo.register_project(self.db, hackathon_id, body.project_id)
        await self.db.commit()

        flipped = await hackathon_repo.auto_start_if_threshold(self.db, hackathon_id)
        if flipped:
            await self.db.commit()

        return {
            "status": "registered",
            "project_id": str(body.project_id),
            "project_title": project["title"],
            "hackathon_id": str(hackathon_id),
            "hackathon_started": flipped,
        }

    async def create(self, body: HackathonCreateRequest) -> HackathonResponse:
        """Create a new hackathon (admin only — caller enforces auth)."""
        hackathon_id = uuid4()
        await hackathon_repo.create_hackathon(self.db, hackathon_id, body.model_dump())
        await self.db.commit()

        hackathon = await hackathon_repo.get_by_id(self.db, hackathon_id)
        return _to_response(hackathon)

    async def update(self, hackathon_id: UUID, body: HackathonUpdateRequest) -> HackathonResponse:
        """Update a hackathon (admin only — caller enforces auth).

        Raises 404 if not found, 422 if no fields provided or invalid status.
        """
        if not await hackathon_repo.hackathon_exists(self.db, hackathon_id):
            raise HTTPException(status_code=404, detail="Hackathon not found")

        updates = body.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=422, detail="No fields to update")

        if "status" in updates and updates["status"] not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail="Invalid status")

        await hackathon_repo.update_hackathon(self.db, hackathon_id, updates)
        await self.db.commit()

        hackathon = await hackathon_repo.get_by_id(self.db, hackathon_id)
        return _to_response(hackathon)


def get_hackathon_service(db: AsyncSession = Depends(get_db)) -> HackathonService:
    """FastAPI dependency that provides a HackathonService bound to the request session."""
    return HackathonService(db)
