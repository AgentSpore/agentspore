"""Serialize mailbox proof changes against concurrent ownership changes."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class EmailProofRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_user(self, user_id: UUID) -> User | None:
        result = await self.db.execute(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def flush(self) -> None:
        await self.db.flush()

    async def email_taken(self, email: str, except_user_id: UUID) -> bool:
        """Case-insensitive uniqueness probe; uses idx_users_email_lower."""
        found = await self.db.scalar(
            select(User.id)
            .where(func.lower(User.email) == email.lower(), User.id != except_user_id)
            .limit(1)
        )
        return found is not None
