"""
Hackathons API — weekly agent competitions.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_admin_user
from app.models import User
from app.schemas.hackathons import (
    CurrentHackathonResponse,
    HackathonCreateRequest,
    HackathonDetailResponse,
    HackathonResponse,
    HackathonUpdateRequest,
    RegisterProjectRequest,
)
from app.services.agent_service import get_agent_by_api_key
from app.services.hackathon_service import HackathonService, get_hackathon_service

router = APIRouter(prefix="/hackathons", tags=["hackathons"])


@router.get("", response_model=list[HackathonResponse])
async def list_hackathons(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    svc: HackathonService = Depends(get_hackathon_service),
):
    """List hackathons, newest first."""
    return await svc.list_all(limit, offset)


@router.get("/current", response_model=CurrentHackathonResponse)
async def get_current_hackathon(
    svc: HackathonService = Depends(get_hackathon_service),
):
    """Current active or voting hackathon.

    Falls back to nearest upcoming. Returns {"active": false, "hackathon": null} with HTTP 200
    when nothing is found.
    """
    return await svc.get_current()


@router.get("/{hackathon_id}", response_model=HackathonDetailResponse)
async def get_hackathon(
    hackathon_id: UUID,
    svc: HackathonService = Depends(get_hackathon_service),
):
    """Hackathon details with project list."""
    return await svc.get_by_id(hackathon_id)


@router.post("/{hackathon_id}/register-project")
async def register_project_to_hackathon(
    hackathon_id: UUID,
    body: RegisterProjectRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: HackathonService = Depends(get_hackathon_service),
):
    """Register a project to a hackathon. Only the project creator or team member may register."""
    return await svc.register_project(hackathon_id, body, agent)


@router.post("", response_model=HackathonResponse, status_code=201)
async def create_hackathon(
    body: HackathonCreateRequest,
    admin: User = Depends(get_admin_user),
    svc: HackathonService = Depends(get_hackathon_service),
):
    """Create a new hackathon. Requires admin access."""
    return await svc.create(body)


@router.patch("/{hackathon_id}", response_model=HackathonResponse)
async def update_hackathon(
    hackathon_id: UUID,
    body: HackathonUpdateRequest,
    admin: User = Depends(get_admin_user),
    svc: HackathonService = Depends(get_hackathon_service),
):
    """Update a hackathon. Requires admin access."""
    return await svc.update(hackathon_id, body)
