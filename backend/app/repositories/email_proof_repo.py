"""Serialize mailbox proof changes against concurrent ownership changes."""

from uuid import UUID

from sqlalchemy import select
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
