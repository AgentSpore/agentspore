"""Agent name availability and suggestions for registration."""

from fastapi import Depends

from app.repositories.agent_name_repo import AgentNameRepository, get_agent_name_repo

MAX_BASE_LENGTH = 190


class AgentNameService:
    def __init__(self, repo: AgentNameRepository) -> None:
        self.repo = repo

    async def is_taken(self, name: str) -> bool:
        return await self.repo.exists(name)

    async def suggest_free(self, name: str, attempts: int = 20) -> str | None:
        """First free ``<name>-N`` (N from 2), or None when all candidates are taken."""
        candidates = [f"{name[:MAX_BASE_LENGTH]}-{n}" for n in range(2, 2 + attempts)]
        taken = await self.repo.taken(candidates)
        return next((c for c in candidates if c not in taken), None)


def get_agent_name_service(
    repo: AgentNameRepository = Depends(get_agent_name_repo),
) -> AgentNameService:
    return AgentNameService(repo)
