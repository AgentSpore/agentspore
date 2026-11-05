"""Persist immutable payout intake under the caller's transaction."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payout_delivery import AllocationIntent, AllocationKey


class PayoutDeliveryRepository:
    """Bind each intake operation to the caller's session and snapshot."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert(self, intent: AllocationIntent) -> dict | None:
        """Insert once without changing a conflicting allocation."""
        values = intent.model_dump()
        row = (
            (
                await self.db.execute(
                    text(
                        "INSERT INTO payout_delivery_intents (producer_namespace, allocation_id, "
                        "genesis_hash, mint, program, source, destination, authority, fee_payer, "
                        "recipient, decimals, raw_amount) "
                        "VALUES (:producer_namespace, :allocation_id, "
                        ":genesis_hash, :mint, :program, "
                        ":source, :destination, :authority, :fee_payer, "
                        ":recipient, :decimals, :raw_amount) "
                        "ON CONFLICT (producer_namespace, allocation_id) DO NOTHING "
                        "RETURNING intent_id, state"
                    ),
                    values,
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def read(self, key: AllocationKey) -> dict | None:
        """Read the current committed winner in a separate statement."""
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM payout_delivery_intents "
                        "WHERE producer_namespace = :producer_namespace "
                        "AND allocation_id = :allocation_id"
                    ),
                    key.model_dump(),
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
