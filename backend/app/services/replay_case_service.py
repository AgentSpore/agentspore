"""Service layer for replay_cases (thin pass-through to repo)."""
from __future__ import annotations

from app.repositories.replay_case_repo import ReplayCaseRepository
from app.schemas.replay_case import ReplayCaseCreate, ReplayCaseResponse


class ReplayCaseService:
    """Business logic for replay case ingestion and retrieval.

    Kept intentionally thin — no cross-cutting concerns beyond validation
    that belong in the endpoint layer.
    """

    def __init__(self, repo: ReplayCaseRepository) -> None:
        self.repo = repo

    async def ingest(self, payload: ReplayCaseCreate) -> ReplayCaseResponse:
        """Persist a sampled trace as a replay case."""
        return await self.repo.create(payload)

    async def list_cases(
        self,
        *,
        agent_handle: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReplayCaseResponse]:
        """Retrieve replay cases with optional agent_handle filter."""
        return await self.repo.list_by_agent(
            agent_handle=agent_handle,
            limit=min(limit, 500),  # hard cap
            offset=offset,
        )
