"""Read-only queries that bind welcome grants to payout intents."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAMESPACE = "welcome"


class WelcomePayoutLinkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def candidates(self, limit: int) -> list[dict]:
        """Undenied grants of an enabled program that have no payout intent yet."""
        rows = await self.db.execute(
            text("""
            SELECT g.id AS grant_id, g.owner_user_id FROM welcome_reward_grants g
            JOIN reward_programs p ON p.id = g.program_id
            WHERE p.enabled AND g.state <> 'denied' AND NOT EXISTS (
                SELECT 1 FROM payout_delivery_intents i
                WHERE i.producer_namespace = :namespace AND i.allocation_id = g.id::text)
            ORDER BY g.created_at, g.id LIMIT :limit"""),
            {"namespace": NAMESPACE, "limit": limit},
        )
        return [dict(row) for row in rows.mappings()]

    async def payout_facts(self, owner: UUID) -> dict | None:
        """Wallet, grant amount and program token identity for one owner."""
        row = (
            (
                await self.db.execute(
                    text("""
            SELECT u.solana_wallet AS wallet, g.id AS grant_id, g.amount_raw,
                   p.mint, p.token_program, p.decimals
            FROM users u
            JOIN welcome_reward_grants g ON g.owner_user_id = u.id AND g.program_id = 'welcome'
            JOIN reward_programs p ON p.id = g.program_id
            WHERE u.id = :owner"""),
                    {"owner": owner},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
