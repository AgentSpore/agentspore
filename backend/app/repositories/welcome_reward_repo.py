"""Welcome reservations share the caller's transaction and stable program lock."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class WelcomeRewardRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def flush_identity(self) -> None:
        """Flush core identity changes before entering optional error handling."""
        await self.db.flush()

    @asynccontextmanager
    async def onboarding_savepoint(self) -> AsyncGenerator[None]:
        """Bound optional ledger queries without changing the caller's timeouts."""
        async with self.db.begin_nested():
            previous = (
                await self.db.execute(
                    text(
                        "SELECT current_setting('lock_timeout'), "
                        "current_setting('statement_timeout')"
                    )
                )
            ).one()
            await self.db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await self.db.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            yield
            await self.db.execute(
                text(
                    "SELECT set_config('lock_timeout', :lock, true), "
                    "set_config('statement_timeout', :statement, true)"
                ),
                {"lock": previous[0], "statement": previous[1]},
            )

    def transaction_usable(self) -> bool:
        """Confirm the enclosing identity transaction survived a ledger error."""
        transaction = self.db.get_transaction()
        return self.db.is_active and transaction is not None and transaction.is_active

    async def enabled(self) -> bool:
        """Skip disabled or absent programs without acquiring reserve locks."""
        return bool(
            await self.db.scalar(text("SELECT enabled FROM reward_programs WHERE id='welcome'"))
        )

    async def user(self, owner: UUID) -> User | None:
        """Read one user without locking."""
        query = select(User).where(User.id == owner).execution_options(populate_existing=True)
        return (await self.db.execute(query)).scalar_one_or_none()

    async def context(
        self, owner: UUID, *, lock: bool = False
    ) -> tuple[User | None, dict, dict | None]:
        """Read the owner/program/grant, optionally locking them in that order."""
        query = select(User).where(User.id == owner).execution_options(populate_existing=True)
        user = (
            await self.db.execute(query.with_for_update() if lock else query)
        ).scalar_one_or_none()
        suffix = " FOR UPDATE" if lock else ""
        program = (
            (
                await self.db.execute(
                    text("SELECT * FROM reward_programs WHERE id='welcome'" + suffix)
                )
            )
            .mappings()
            .one()
        )
        grant = (
            (
                await self.db.execute(
                    text(
                        "SELECT * FROM welcome_reward_grants WHERE program_id='welcome' "
                        "AND owner_user_id=:owner" + suffix
                    ),
                    {"owner": owner},
                )
            )
            .mappings()
            .first()
        )
        return user, dict(program), dict(grant) if grant else None

    async def apply(self, owner: UUID) -> None:
        """Insert a conditional application under the already-held program lock."""
        await self.db.execute(
            text("""
            INSERT INTO welcome_reward_grants(program_id,owner_user_id,source_event)
            VALUES ('welcome',:owner,:owner) ON CONFLICT(program_id,owner_user_id) DO NOTHING
        """),
            {"owner": owner},
        )

    async def decision(self, grant_id: UUID) -> dict | None:
        """Read the latest serialized decision against the current database clock."""
        row = (
            (
                await self.db.execute(
                    text("""
            SELECT *, issued_at<=clock_timestamp() AND expires_at>clock_timestamp() AS current
            FROM reward_eligibility_decisions WHERE grant_id=:id ORDER BY revision DESC LIMIT 1
        """),
                    {"id": grant_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def free_funding(self) -> int:
        """Subtract all frozen and available obligations from immutable allocations."""
        return int(
            await self.db.scalar(
                text("""
            SELECT COALESCE((SELECT sum(amount_raw) FROM reward_funding_entries
                             WHERE program_id='welcome'),0)
                 - COALESCE((SELECT sum(amount_raw) FROM welcome_reward_grants
                             WHERE program_id='welcome' AND state IN ('frozen','available')),0)
        """)
            )
        )

    async def beneficiary_used(self, key: str, grant_id: UUID) -> bool:
        """Check canonical beneficiary uniqueness while holding the program lock."""
        return bool(
            await self.db.scalar(
                text("""
            SELECT EXISTS(SELECT 1 FROM welcome_reward_grants
              WHERE program_id='welcome' AND beneficiary_key=:key AND id<>:id)
        """),
                {"key": key, "id": grant_id},
            )
        )

    async def freeze(self, grant: dict, program: dict, decision: dict) -> None:
        """Snapshot admitted terms and reserve funds in the caller transaction."""
        await self.db.execute(
            text("""
            UPDATE welcome_reward_grants SET state='frozen', amount_raw=:amount,
                tariff_version=:version, beneficiary_key=:key, decision_id=:decision
            WHERE id=:id AND state='application'
        """),
            {
                "id": grant["id"],
                "amount": program["amount_raw"],
                "version": program["tariff_version"],
                "key": decision["beneficiary_key"],
                "decision": decision["id"],
            },
        )

    async def available(self, grant_id: UUID) -> None:
        """Unlock an eligible grant without releasing its reserved backing."""
        await self.db.execute(
            text(
                "UPDATE welcome_reward_grants SET state='available' WHERE id=:id AND state='frozen'"
            ),
            {"id": grant_id},
        )

    async def limit_lock_wait(self) -> None:
        """Bound waits on the shared reward locks; the timeout dies with the transaction."""
        await self.db.execute(text("SET LOCAL lock_timeout = '10s'"))

    async def decision_clock(self) -> datetime:
        """Reject stale transaction snapshots before reading or locking decisions."""
        row = (
            await self.db.execute(
                text("SELECT current_setting('transaction_isolation'), clock_timestamp()")
            )
        ).one()
        if row[0] != "read committed":
            raise ValueError("Reward mutations require READ COMMITTED")
        return row[1]

    async def decision_evidence(self, evidence_ref: str) -> dict | None:
        """Look up the global evidence identity after acquiring shared locks."""
        row = (
            (
                await self.db.execute(
                    text("SELECT * FROM reward_eligibility_decisions WHERE evidence_ref=:evidence"),
                    {"evidence": evidence_ref},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def insert_decision(self, payload: dict) -> dict:
        """Append evidence; the existing trigger alone assigns its revision."""
        row = (
            (
                await self.db.execute(
                    text("""
            INSERT INTO reward_eligibility_decisions
                (grant_id,owner_user_id,source_event,policy_version,outcome,beneficiary_key,
                 authority_ref,evidence_ref,email_proof_at,issued_at,expires_at)
            VALUES (:grant_id,:owner_user_id,:source_event,:policy_version,:outcome,
                    :beneficiary_key,:authority_ref,:evidence_ref,:email_proof_at,
                    :issued_at,:expires_at)
            RETURNING id,revision
        """),
                    payload,
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def payout_progress(self, grant_id: UUID | None) -> dict | None:
        """Delivery stage of one grant's payout intent; None before an intent exists."""
        if grant_id is None:
            return None
        row = (
            (
                await self.db.execute(
                    text("""
            SELECT a.first_signature AS signature, c.intent_id IS NOT NULL AS confirmed
            FROM payout_delivery_intents i
            LEFT JOIN payout_delivery_attempts a ON a.intent_id = i.intent_id
            LEFT JOIN payout_delivery_confirmations c ON c.intent_id = i.intent_id
            WHERE i.producer_namespace = 'welcome' AND i.allocation_id = :grant
        """),
                    {"grant": str(grant_id)},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def wallet_paid_to_another(self, wallet: str | None, grant_id: UUID | None) -> bool:
        """True when a different welcome grant already has a payout intent for this wallet."""
        if not wallet:
            return False
        return bool(
            await self.db.scalar(
                text("""
            SELECT EXISTS(SELECT 1 FROM payout_delivery_intents
                          WHERE producer_namespace = 'welcome' AND recipient = :wallet
                            AND allocation_id <> :grant)
        """),
                {"wallet": wallet, "grant": str(grant_id) if grant_id else ""},
            )
        )
