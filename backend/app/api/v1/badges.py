"""Badge system for agents."""

from fastapi import APIRouter

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
    """Get badges for a specific agent."""
    rows = await badge_repo.get_agent_badges(db, agent_id)
    return [AgentBadge(**r) for r in rows]
