"""Educational accounting responses, independent from real payment balances."""

from typing import Literal

from pydantic import BaseModel


class LearningBudgetResponse(BaseModel):
    """An authenticated agent's explicitly activated educational budget."""

    available: int = 0
    reserved: int = 0
    initial_grant_applied: bool = False


class LearningReservation(BaseModel):
    """Immutable price and current settlement state for a reviewed task."""

    amount: int
    status: Literal["reserved", "captured", "refunded"]
