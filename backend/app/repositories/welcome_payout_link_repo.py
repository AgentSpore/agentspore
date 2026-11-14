"""Read-only queries that bind welcome grants to payout intents."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAMESPACE = "welcome"

CANDIDATES = """
    SELECT g.id AS grant_id, g.owner_user_id, g.created_at
    FROM welcome_reward_grants g
    JOIN reward_programs p ON p.id = g.program_id
    JOIN users u ON u.id = g.owner_user_id
    WHERE p.enabled AND u.solana_wallet IS NOT NULL
      AND (g.state IN ('frozen', 'available') OR (g.state = 'application' AND EXISTS (
            SELECT 1 FROM reward_eligibility_decisions d
            WHERE d.grant_id = g.id AND d.outcome = 'allow'
              AND d.issued_at <= clock_timestamp() AND d.expires_at > clock_timestamp()
              AND d.revision = (SELECT max(revision) FROM reward_eligibility_decisions
                                WHERE grant_id = g.id))))
      AND NOT EXISTS (
            SELECT 1 FROM payout_delivery_intents i
            WHERE i.producer_namespace = :namespace AND i.allocation_id = g.id::text)
      AND (CAST(:after_at AS timestamptz) IS NULL
           OR (g.created_at, g.id) > (CAST(:after_at AS timestamptz), CAST(:after_id AS uuid)))
    ORDER BY g.created_at, g.id LIMIT :limit"""


class WelcomePayoutLinkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def candidates(self, limit: int, after: tuple | None = None) -> list[dict]:
        """Grants that could be payable, in keyset order after `(created_at, id)`."""
        after_at, after_id = after or (None, None)
        rows = await self.db.execute(
            text(CANDIDATES),
            {"namespace": NAMESPACE, "limit": limit, "after_at": after_at, "after_id": after_id},
        )
        return [dict(row) for row in rows.mappings()]

    async def wallet_rewarded_elsewhere(self, wallet: str, grant_id: UUID) -> bool:
        """True when another welcome grant already has a payout intent for this wallet."""
        return bool(
            await self.db.scalar(
                text("""
            SELECT EXISTS(SELECT 1 FROM payout_delivery_intents
                          WHERE producer_namespace=:namespace AND recipient=:wallet
                            AND allocation_id<>CAST(:grant_id AS text))"""),
                {"namespace": NAMESPACE, "wallet": wallet, "grant_id": str(grant_id)},
            )
        )

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
