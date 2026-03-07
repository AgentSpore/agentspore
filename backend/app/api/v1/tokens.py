"""API для работы с токенами."""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.tokens import BalanceResponse

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(current_user: CurrentUser):
    """Получить баланс токенов текущего пользователя."""
    return BalanceResponse(balance=current_user.token_balance)
