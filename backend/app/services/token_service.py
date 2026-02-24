"""Сервис управления токенами."""

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TokenAction, TokenTransaction, TOKEN_REWARDS, User


class TokenService:
    """Сервис для работы с токенами."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def award_tokens(
        self,
        user_id: uuid.UUID,
        action: TokenAction,
        idea_id: uuid.UUID | None = None,
        custom_amount: int | None = None,
    ) -> TokenTransaction:
        """Начислить токены пользователю за действие."""
        amount = custom_amount if custom_amount is not None else TOKEN_REWARDS[action]

        # Создаём транзакцию
        transaction = TokenTransaction(
            user_id=user_id,
            idea_id=idea_id,
            amount=amount,
            action=action.value if hasattr(action, 'value') else action,
        )
        self.db.add(transaction)

        # Обновляем баланс пользователя
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one()
        user.token_balance += amount

        await self.db.flush()
        return transaction

    async def get_balance(self, user_id: uuid.UUID) -> int:
        """Получить баланс токенов пользователя."""
        result = await self.db.execute(select(User.token_balance).where(User.id == user_id))
        balance = result.scalar_one_or_none()
        return balance or 0

    async def get_history(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TokenTransaction]:
        """Получить историю транзакций пользователя."""
        result = await self.db.execute(
            select(TokenTransaction)
            .where(TokenTransaction.user_id == user_id)
            .order_by(TokenTransaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_leaderboard(
        self,
        idea_id: uuid.UUID,
        limit: int = 10,
    ) -> list[dict]:
        """Получить топ контрибьюторов для идеи."""
        from sqlalchemy import func

        result = await self.db.execute(
            select(
                TokenTransaction.user_id,
                func.sum(TokenTransaction.amount).label("total_tokens"),
            )
            .where(TokenTransaction.idea_id == idea_id)
            .group_by(TokenTransaction.user_id)
            .order_by(func.sum(TokenTransaction.amount).desc())
            .limit(limit)
        )

        leaderboard = []
        for row in result.all():
            user_result = await self.db.execute(
                select(User.name, User.avatar_url).where(User.id == row.user_id)
            )
            user_data = user_result.first()
            leaderboard.append({
                "user_id": str(row.user_id),
                "name": user_data.name if user_data else "Unknown",
                "avatar_url": user_data.avatar_url if user_data else None,
                "total_tokens": row.total_tokens,
            })

        return leaderboard
