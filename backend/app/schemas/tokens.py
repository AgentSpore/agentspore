"""Token schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models import TokenAction


class BalanceResponse(BaseModel):
    balance: int


class TransactionResponse(BaseModel):
    id: uuid.UUID
    amount: int
    action: TokenAction
    idea_id: uuid.UUID | None
    created_at: datetime


class LeaderboardEntry(BaseModel):
    user_id: str
    name: str
    avatar_url: str | None
    total_tokens: int
