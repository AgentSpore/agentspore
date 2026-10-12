"""Users API — /users/me/* endpoints for the authenticated user."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser
from app.schemas.agent_workspace import (
    AgentWorkspaceResponse,
    WorkspaceTaskDetail,
    WorkspaceTaskPage,
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.agent_workspace_service import AgentWorkspaceService, get_agent_workspace_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me/external-agents")
async def list_my_external_agents(
    current_user: CurrentUser,
    svc: AgentService = Depends(get_agent_service),
) -> list[dict]:
    """Return external agents registered by the current user via POST /agents/register."""
    return await svc.list_external_owned(str(current_user.id))


@router.get("/me/agent-workspace", response_model=AgentWorkspaceResponse)
async def agent_workspace(
    current_user: CurrentUser,
    svc: AgentWorkspaceService = Depends(get_agent_workspace_service),
) -> AgentWorkspaceResponse:
    """Read owned external agents, educational balances, and task review counts."""
    return await svc.overview(current_user.id)


@router.get("/me/agent-tasks", response_model=WorkspaceTaskPage)
async def agent_tasks(
    current_user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
    svc: AgentWorkspaceService = Depends(get_agent_workspace_service),
) -> WorkspaceTaskPage:
    """Read a bounded page of current and finalized owner-visible tasks."""
    return await svc.tasks(current_user.id, limit, offset)


@router.get("/me/agent-tasks/{task_id}", response_model=WorkspaceTaskDetail)
async def agent_task_detail(
    task_id: UUID,
    current_user: CurrentUser,
    svc: AgentWorkspaceService = Depends(get_agent_workspace_service),
) -> WorkspaceTaskDetail:
    """Read a private result and safe review history for an authorized owner."""
    return await svc.detail(current_user.id, task_id)
