"""Educational grants and settlement composed with task review transactions."""

from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.learning_budget_repo import LearningBudgetRepository
from app.schemas.learning_budget import LearningBudgetResponse


class LearningBudgetService:
    """Require locked accounts and a live reservation before moving educational units."""

    def __init__(self, db: AsyncSession):
        self.repo = LearningBudgetRepository(db)

    async def read(self, actor: UUID) -> LearningBudgetResponse:
        """Return zero for an absent account without implicitly activating it."""
        return LearningBudgetResponse(**(await self.repo.read(actor) or {}))

    async def activate(self, actor: UUID) -> LearningBudgetResponse:
        """Grant educational units exactly once for the authenticated principal."""
        return LearningBudgetResponse(**await self.repo.grant(actor))

    async def reserve(self, task_id: UUID, requester: UUID, amount: int) -> None:
        """Reject unaffordable tasks before changing either educational balance."""
        accounts = await self.repo.lock_accounts([requester])
        if accounts[str(requester)]["available"] < amount:
            raise HTTPException(status_code=409, detail="Insufficient educational credits")
        await self.repo.reserve(task_id, requester, amount)

    async def settle(self, task: dict, state: str) -> None:
        """Capture or refund once, retaining reservations during nonfinal transitions."""
        if state not in {"accepted", "rejected", "cancelled"}:
            return
        reservation = await self.repo.reservation(task["id"], lock=True)
        if not reservation or reservation["status"] != "reserved":
            return
        recipient = task["claimed_by_agent_id"] if state == "accepted" else None
        participants = [reservation["requester_agent_id"]]
        if recipient:
            participants.append(recipient)
        await self.repo.lock_accounts(participants)
        await self.repo.settle(reservation, recipient)


def get_learning_budget_service(db: AsyncSession = Depends(get_db)) -> LearningBudgetService:
    """Inject the transaction owned by the request dependency."""
    return LearningBudgetService(db)
