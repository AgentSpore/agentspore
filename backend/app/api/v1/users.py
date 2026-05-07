"""Users API — /users/me/* endpoints for the authenticated user."""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser
from app.services.agent_service import AgentService, get_agent_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me/external-agents")
async def list_my_external_agents(
    current_user: CurrentUser,
    svc: AgentService = Depends(get_agent_service),
) -> list[dict]:
    """Return external agents registered by the current user via POST /agents/register."""
    return await svc.list_external_owned(str(current_user.id))
