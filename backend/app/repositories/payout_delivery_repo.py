"""Persist immutable payout intake under the caller's transaction."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payout_delivery import AllocationIntent, AllocationKey, ClaimKey


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

    async def claim_budget(self) -> None:
        """Bound transaction-local database statements and lock waits."""
        await self.db.execute(text("SET LOCAL lock_timeout = '250ms'"))
        await self.db.execute(text("SET LOCAL statement_timeout = '1000ms'"))

    async def read_claim(self, key: ClaimKey) -> dict | None:
        """Inspect the stored fence without repairing an expired claim."""
        await self.claim_budget()
        row = (
            (
                await self.db.execute(
                    text("SELECT * FROM payout_delivery_claims WHERE intent_id = :intent_id"),
                    key.model_dump(),
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def mutate_claim(self, command: str, request: ClaimKey) -> tuple[bool, dict | None]:
        """Lock the immutable parent before applying one bounded child mutation."""
        await self.claim_budget()
        values = request.model_dump()
        parent = await self.db.scalar(
            text(
                "SELECT intent_id FROM payout_delivery_intents "
                "WHERE intent_id = :intent_id FOR UPDATE NOWAIT"
            ),
            values,
        )
        if parent is None:
            return False, None
        if command == "claim":
            sql = (
                "INSERT INTO "
                "payout_delivery_claims(intent_id, epoch, state, worker_id, expires_at) "
                "VALUES (:intent_id, 1, 'claimed', :worker_id, "
                "clock_timestamp() + :ttl_seconds * interval '1 second') "
                "ON CONFLICT (intent_id) DO UPDATE SET epoch = payout_delivery_claims.epoch + 1, "
                "state = 'claimed', worker_id = "
                "EXCLUDED.worker_id, expires_at = EXCLUDED.expires_at "
                "WHERE payout_delivery_claims.state = 'ready'"
            )
        else:
            sql = self.fenced_claim_sql(command)
        row = (await self.db.execute(text(sql + " RETURNING *"), values)).mappings().first()
        return True, dict(row) if row else None

    @staticmethod
    def fenced_claim_sql(command: str) -> str:
        """Keep state and complementary live/expired predicates on every fenced write."""
        change = (
            "expires_at = GREATEST(expires_at, clock_timestamp() + "
            ":ttl_seconds * interval '1 second')"
            if command == "renew"
            else "state = 'ready', worker_id = NULL, expires_at = NULL"
        )
        comparison = "<=" if command == "recover" else ">"
        return (
            "UPDATE payout_delivery_claims SET "
            + change
            + " WHERE intent_id = :intent_id AND worker_id = :worker_id AND epoch = :epoch "
            "AND state = 'claimed' AND expires_at " + comparison + " clock_timestamp()"
        )
