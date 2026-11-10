"""Persist immutable payout intake under the caller's transaction."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.payout_delivery import (
    AllocationIntent,
    AllocationKey,
    ClaimKey,
    ConfirmationEvidence,
)


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

    async def lock_intent(self, key: ClaimKey) -> dict | None:
        """Read immutable fields under the same bounded parent-first preparation lock."""
        await self.claim_budget()
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM payout_delivery_intents "
                        "WHERE intent_id = :intent_id FOR UPDATE NOWAIT"
                    ),
                    {"intent_id": key.intent_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def live_preparation_fence(self, key: ClaimKey) -> bool:
        """Read only a matching live claimed generation without altering it."""
        return (
            await self.db.scalar(
                text(
                    "SELECT intent_id FROM payout_delivery_claims WHERE intent_id = :intent_id "
                    "AND worker_id = :worker_id AND epoch = :epoch AND state = 'claimed' "
                    "AND expires_at > clock_timestamp()"
                ),
                key.model_dump(),
            )
            is not None
        )

    async def read_attempt(self, key: ClaimKey) -> dict | None:
        """Load the committed original bytes; future senders must retain this identity."""
        await self.claim_budget()
        row = (
            (
                await self.db.execute(
                    text("SELECT * FROM payout_delivery_attempts WHERE intent_id = :intent_id"),
                    {"intent_id": key.intent_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def prepare_fence(self, key: ClaimKey) -> bool:
        """Leave claimed only under its exact live fence, retaining expiry provenance."""
        return (
            await self.db.scalar(
                text(
                    "UPDATE payout_delivery_claims SET state "
                    "= 'prepared' WHERE intent_id = :intent_id "
                    "AND worker_id = :worker_id AND epoch = :epoch AND state = 'claimed' "
                    "AND expires_at > clock_timestamp() RETURNING intent_id"
                ),
                key.model_dump(),
            )
            is not None
        )

    async def insert_attempt(self, values: dict) -> None:
        """Insert the full immutable attempt under the caller's atomic prepared transaction."""
        identity = await self.db.scalar(
            text(
                "INSERT INTO payout_delivery_attempts(intent_id, epoch, worker_id, signed_bytes, "
                "first_signature, recent_blockhash, last_valid_block_height) VALUES "
                "(:intent_id, :epoch, :worker_id, :signed_bytes, :first_signature, "
                ":recent_blockhash, :last_valid_block_height) RETURNING intent_id"
            ),
            values,
        )
        if identity is None:
            raise RuntimeError("Attempt insert did not return an identity")

    async def read_reconcile_row(self, key: ClaimKey) -> dict | None:
        """Join the immutable attempt, its intent and any stored confirmation."""
        await self.claim_budget()
        row = (
            (
                await self.db.execute(
                    text(
                        "SELECT i.genesis_hash, i.mint, i.program, i.source, i.destination, "
                        "i.authority, i.fee_payer, i.recipient, i.decimals, i.raw_amount, "
                        "a.signed_bytes, a.first_signature, c.finalized_slot, "
                        "c.verifier_version, c.evidence_digest "
                        "FROM payout_delivery_attempts a "
                        "JOIN payout_delivery_intents i ON i.intent_id = a.intent_id "
                        "LEFT JOIN payout_delivery_confirmations c ON c.intent_id = a.intent_id "
                        "WHERE a.intent_id = :intent_id"
                    ),
                    {"intent_id": key.intent_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def insert_confirmation(self, key: ClaimKey, evidence: ConfirmationEvidence) -> None:
        """Insert the first verified fact; a duplicate raises instead of overwriting."""
        await self.db.execute(
            text(
                "INSERT INTO payout_delivery_confirmations(intent_id, finalized_slot, "
                "verifier_version, evidence_digest) VALUES "
                "(:intent_id, :finalized_slot, :verifier_version, :evidence_digest)"
            ),
            {"intent_id": key.intent_id, **evidence.model_dump()},
        )

    async def list_phases(self, limit: int) -> list[dict]:
        """List intent ids with a delivery phase, unfinished first; no amounts or wallets."""
        rows = await self.db.execute(
            text(
                "SELECT i.intent_id, CASE WHEN c.intent_id IS NOT NULL THEN 'confirmed' "
                "WHEN a.intent_id IS NOT NULL THEN 'prepared' "
                "WHEN cl.state = 'claimed' AND cl.expires_at <= clock_timestamp() THEN 'expired' "
                "WHEN cl.state = 'claimed' THEN 'claimed' ELSE 'unclaimed' END AS phase "
                "FROM payout_delivery_intents i "
                "LEFT JOIN payout_delivery_claims cl ON cl.intent_id = i.intent_id "
                "LEFT JOIN payout_delivery_attempts a ON a.intent_id = i.intent_id "
                "LEFT JOIN payout_delivery_confirmations c ON c.intent_id = i.intent_id "
                "ORDER BY (c.intent_id IS NOT NULL), i.intent_id LIMIT :limit"
            ),
            {"limit": limit},
        )
        return [dict(row) for row in rows.mappings()]
