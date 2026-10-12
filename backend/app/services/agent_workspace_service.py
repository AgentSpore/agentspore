"""Owner read operations composed without agent impersonation or account writes."""

from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.agent_workspace_repo import AgentWorkspaceRepository
from app.schemas.agent_workspace import (
    AgentWorkspaceResponse,
    WorkspaceTaskDetail,
    WorkspaceTaskPage,
)


class AgentWorkspaceService:
    """Keep every read bound to the repository's owner-scoped session."""

    def __init__(self, db: AsyncSession):
        self.repo = AgentWorkspaceRepository(db)

    async def overview(self, owner: UUID) -> AgentWorkspaceResponse:
        """Read observed agents and educational balances without activating accounts."""
        return AgentWorkspaceResponse(
            agents=await self.repo.agents(owner), review_counts=await self.repo.review_counts(owner)
        )

    async def tasks(self, owner: UUID, limit: int, offset: int) -> WorkspaceTaskPage:
        """Return bounded metadata for current and finalized tasks."""
        items, total = await self.repo.tasks(owner, limit, offset)
        return WorkspaceTaskPage(items=items, total=total, limit=limit, offset=offset)

    async def detail(self, owner: UUID, task: UUID) -> WorkspaceTaskDetail:
        """Hide existence of inaccessible tasks before exposing results or history."""
        row = await self.repo.detail(owner, task)
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return WorkspaceTaskDetail(**row, history=await self.repo.history(owner, task))


def get_agent_workspace_service(db: AsyncSession = Depends(get_db)) -> AgentWorkspaceService:
    """Reuse the request transaction for all workspace reads."""
    return AgentWorkspaceService(db)
