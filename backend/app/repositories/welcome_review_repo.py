"""Batch reads for operator review of undecided welcome applications."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.welcome_auto_decision_repo import UNDECIDED


class WelcomeReviewRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def pending(self, limit: int, only: UUID | None = None) -> list[dict]:
        """Oldest undecided grants with their age on the database clock; `only` picks one."""
        rows = await self.db.execute(
            text(f"""
            SELECT g.id AS grant_id, g.owner_user_id,
                   floor(extract(epoch FROM clock_timestamp() - g.created_at))::bigint AS age
            FROM welcome_reward_grants g
            WHERE {UNDECIDED} AND (CAST(:only AS uuid) IS NULL OR g.id = CAST(:only AS uuid))
            ORDER BY g.created_at, g.id LIMIT :limit"""),
            {"limit": limit, "only": only},
        )
        return [dict(row) for row in rows.mappings()]

    async def users(self, ids: Collection[UUID]) -> dict[UUID, User]:
        """Users by id in one query."""
        query = select(User).where(User.id.in_(ids)).execution_options(populate_existing=True)
        return {user.id: user for user in (await self.db.execute(query)).scalars()}

    async def taken_keys(self, keys: Collection[str]) -> set[str]:
        """Beneficiary keys already held by a grant or carrying an allow decision."""
        rows = await self.db.execute(
            text("""
            SELECT beneficiary_key FROM welcome_reward_grants
              WHERE program_id='welcome' AND beneficiary_key = ANY(CAST(:keys AS text[]))
            UNION
            SELECT beneficiary_key FROM reward_eligibility_decisions
              WHERE outcome='allow' AND beneficiary_key = ANY(CAST(:keys AS text[]))"""),
            {"keys": list(keys)},
        )
        return {row[0] for row in rows}

    async def paid_wallets(self, wallets: Collection[str]) -> set[str]:
        """Wallets that already have a welcome payout intent."""
        rows = await self.db.execute(
            text("""
            SELECT recipient FROM payout_delivery_intents
            WHERE producer_namespace='welcome' AND recipient = ANY(CAST(:wallets AS text[]))"""),
            {"wallets": list(wallets)},
        )
        return {row[0] for row in rows}
