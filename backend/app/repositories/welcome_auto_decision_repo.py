"""Queries that bound automatic welcome approvals."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class WelcomeAutoDecisionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock(self) -> None:
        """Serialize automatic runs; the lock is held until the transaction ends."""
        await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtext('welcome_auto_decide'))"))

    async def approvals_since_day(self, authority_ref: str) -> int:
        """Count automatic allows issued within the rolling 24 hours."""
        return int(
            await self.db.scalar(
                text("""
            SELECT count(*) FROM reward_eligibility_decisions
            WHERE authority_ref=:ref AND outcome='allow'
              AND issued_at > clock_timestamp() - interval '24 hours'
        """),
                {"ref": authority_ref},
            )
        )

    async def undecided_applications(self) -> list[dict]:
        """Applications that have no decision of any kind, oldest first."""
        rows = (
            await self.db.execute(
                text("""
            SELECT g.* FROM welcome_reward_grants g
            WHERE g.program_id='welcome' AND g.state='application'
              AND NOT EXISTS (SELECT 1 FROM reward_eligibility_decisions d WHERE d.grant_id=g.id)
            ORDER BY g.created_at, g.id
        """)
            )
        ).mappings()
        return [dict(row) for row in rows]

    async def beneficiary_approved_elsewhere(self, key: str, grant_id: UUID) -> bool:
        """True when another grant holds the key or carries any allow decision for it."""
        return bool(
            await self.db.scalar(
                text("""
            SELECT EXISTS(SELECT 1 FROM welcome_reward_grants
                          WHERE program_id='welcome' AND beneficiary_key=:key AND id<>:id)
                OR EXISTS(SELECT 1 FROM reward_eligibility_decisions
                          WHERE outcome='allow' AND beneficiary_key=:key AND grant_id<>:id)
        """),
                {"key": key, "id": grant_id},
            )
        )
