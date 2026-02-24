"""API для работы с токенами."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime

from app.api.deps import CurrentUser, TokenSvc
from app.models import TokenAction

router = APIRouter(prefix="/tokens", tags=["tokens"])


# === Schemas ===


class BalanceResponse(BaseModel):
    """Баланс токенов."""

    balance: int


class TransactionResponse(BaseModel):
    """Транзакция токенов."""

    id: uuid.UUID
    amount: int
    action: TokenAction
    idea_id: uuid.UUID | None
    created_at: datetime


class LeaderboardEntry(BaseModel):
    """Запись в таблице лидеров."""

    user_id: str
    name: str
    avatar_url: str | None
    total_tokens: int


# === Endpoints ===


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(current_user: CurrentUser):
    """Получить баланс токенов текущего пользователя."""
    return BalanceResponse(balance=current_user.token_balance)


@router.get("/history", response_model=list[TransactionResponse])
async def get_history(
    current_user: CurrentUser,
    token_service: TokenSvc,
    limit: int = 50,
    offset: int = 0,
):
    """Получить историю транзакций."""
    transactions = await token_service.get_history(
        user_id=current_user.id,
        limit=limit,
        offset=offset,
    )

    return [
        TransactionResponse(
            id=t.id,
            amount=t.amount,
            action=t.action,
            idea_id=t.idea_id,
            created_at=t.created_at,
        )
        for t in transactions
    ]


@router.get("/leaderboard/{idea_id}", response_model=list[LeaderboardEntry])
async def get_leaderboard(
    idea_id: uuid.UUID,
    token_service: TokenSvc,
    limit: int = 10,
):
    """Получить топ контрибьюторов для идеи."""
    leaders = await token_service.get_leaderboard(
        idea_id=idea_id,
        limit=limit,
    )

    return [
        LeaderboardEntry(
            user_id=entry["user_id"],
            name=entry["name"],
            avatar_url=entry["avatar_url"],
            total_tokens=entry["total_tokens"],
        )
        for entry in leaders
    ]
