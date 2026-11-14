"""Serialize mailbox proof changes against concurrent ownership changes."""

from uuid import UUID

from sqlalchemy import func, select, text
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

    async def welcome_reward_unpaid(self, user_id: UUID) -> bool:
        """True while the owner's welcome grant is reserved or allowed and not yet paid out."""
        return bool(
            await self.db.scalar(
                text("""
            SELECT EXISTS(
              SELECT 1 FROM welcome_reward_grants g
              WHERE g.program_id = 'welcome' AND g.owner_user_id = :owner
                AND (g.state IN ('frozen', 'available') OR (g.state = 'application' AND (
                      SELECT d.outcome = 'allow' AND d.issued_at <= clock_timestamp()
                             AND d.expires_at > clock_timestamp()
                      FROM reward_eligibility_decisions d
                      WHERE d.grant_id = g.id ORDER BY d.revision DESC LIMIT 1)))
                AND NOT EXISTS (
                      SELECT 1 FROM payout_delivery_intents i
                      JOIN payout_delivery_confirmations c ON c.intent_id = i.intent_id
                      WHERE i.producer_namespace = 'welcome' AND i.allocation_id = g.id::text))
        """),
                {"owner": user_id},
            )
        )
