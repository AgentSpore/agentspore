"""Users API — /users/me/* endpoints for the authenticated user."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from redis import asyncio as aioredis

from app.api.deps import CurrentUser, EmailProofSession, WelcomeRewardSession
from app.core.redis_client import get_redis
from app.repositories.email_proof_repo import EmailProofRepository
from app.schemas.agent_workspace import (
    AgentWorkspaceResponse,
    WorkspaceTaskDetail,
    WorkspaceTaskPage,
)
from app.schemas.email_change import EmailChangeConfirm, EmailChangeRequest, EmailChangeResponse
from app.schemas.welcome_rewards import WelcomeRewardView
from app.services.agent_service import AgentService, get_agent_service
from app.services.agent_workspace_service import AgentWorkspaceService, get_agent_workspace_service
from app.services.email_change_service import EmailChangeService
from app.services.email_service import EmailService, get_email_service
from app.services.welcome_reward_service import WelcomeRewardService, get_welcome_reward_service

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


@router.get("/me/rewards/welcome", response_model=WelcomeRewardView)
async def welcome_reward(
    current_user: CurrentUser,
    svc: WelcomeRewardService = Depends(get_welcome_reward_service),
) -> WelcomeRewardView:
    """Read only the authenticated owner's conditional or funded welcome status."""
    return await svc.read(current_user.id)


@router.post("/me/rewards/welcome/application", response_model=WelcomeRewardView)
async def apply_welcome_reward(
    current_user: CurrentUser,
    db: WelcomeRewardSession,
) -> WelcomeRewardView:
    """Retry only the authenticated owner's conditional application, without funding it."""
    return await get_welcome_reward_service(db).apply(current_user.id)


@router.post("/me/email/change", response_model=EmailChangeResponse, status_code=202)
async def request_email_change(
    data: EmailChangeRequest,
    user: CurrentUser,
    db: EmailProofSession,
    redis: aioredis.Redis = Depends(get_redis),
    mail: EmailService = Depends(get_email_service),
) -> EmailChangeResponse:
    """Send a confirmation link to the new address; the current email stays active until then."""
    service = EmailChangeService(EmailProofRepository(db), redis, mail)
    return EmailChangeResponse(
        message=await service.request(user.id, data.new_email, data.current_password)
    )


@router.post("/me/email/change/confirm", response_model=EmailChangeResponse)
async def confirm_email_change(
    data: EmailChangeConfirm,
    user: CurrentUser,
    db: EmailProofSession,
    redis: aioredis.Redis = Depends(get_redis),
    mail: EmailService = Depends(get_email_service),
) -> EmailChangeResponse:
    """Single-use POST confirm (a mail preview GET cannot consume the link)."""
    await EmailChangeService(EmailProofRepository(db), redis, mail).confirm(user.id, data.token)
    return EmailChangeResponse(message="Email changed.")
