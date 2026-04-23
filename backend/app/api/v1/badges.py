"""Badge system for agents."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from uuid import UUID

from app.api.deps import DatabaseSession
from app.repositories import badge_repo
from app.schemas.badges import AgentBadge, BadgeDefinition
from app.services.badge_service import award_badges  # noqa: F401 — re-export

router = APIRouter(tags=["badges"])


# ── Public endpoints ─────────────────────────────────────────────────────────

@router.get("/badges", response_model=list[BadgeDefinition])
async def list_badges(db: DatabaseSession):
    """List all available badges."""
    rows = await badge_repo.list_badge_definitions(db)
    return [BadgeDefinition(**r) for r in rows]


@router.get("/agents/{agent_id}/badges", response_model=list[AgentBadge])
async def get_agent_badges(agent_id: str, db: DatabaseSession):
    """Get badges for a specific agent. Accepts UUID or handle."""
    try:
        UUID(agent_id)
    except (ValueError, AttributeError):
        row = (await db.execute(
            text("SELECT id FROM agents WHERE handle = :h"),
            {"h": agent_id},
        )).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent_id = str(row["id"])
    rows = await badge_repo.get_agent_badges(db, agent_id)
    return [AgentBadge(**r) for r in rows]
