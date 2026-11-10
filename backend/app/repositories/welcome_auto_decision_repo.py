"""Queries that bound automatic welcome approvals."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UNDECIDED = """
    g.program_id='welcome' AND g.state='application'
    AND NOT EXISTS (SELECT 1 FROM reward_eligibility_decisions d WHERE d.grant_id=g.id)
"""


class WelcomeAutoDecisionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock(self) -> None:
        """Serialize automatic runs; the lock is held until the transaction ends."""
        await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtext('welcome_auto_decide'))"))

    async def approvals_since_day(self, authority_prefix: str) -> int:
        """Count automatic allows (any rule version) issued within the rolling 24 hours."""
        return int(
            await self.db.scalar(
                text("""
            SELECT count(*) FROM reward_eligibility_decisions
            WHERE starts_with(authority_ref, :prefix) AND outcome='allow'
              AND issued_at > clock_timestamp() - interval '24 hours'
        """),
                {"prefix": authority_prefix},
            )
        )

    async def eligible_applications(self, min_age_days: int) -> list[dict]:
        """Undecided applications with a GitHub identity old enough at application time."""
        rows = (
            await self.db.execute(
                text(f"""
            SELECT g.* FROM welcome_reward_grants g JOIN users u ON u.id=g.owner_user_id
            WHERE {UNDECIDED} AND u.oauth_provider='github'
              AND u.oauth_account_created_at IS NOT NULL
              AND g.created_at - u.oauth_account_created_at >= make_interval(days => :days)
            ORDER BY g.created_at, g.id
        """),
                {"days": min_age_days},
            )
        ).mappings()
        return [dict(row) for row in rows]

    async def undecided_count(self) -> int:
        """All applications still waiting for any decision."""
        return int(
            await self.db.scalar(
                text(f"SELECT count(*) FROM welcome_reward_grants g WHERE {UNDECIDED}")
            )
        )

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
