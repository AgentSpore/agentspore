"""Batch reads for operator review of undecided welcome applications."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.welcome_auto_decision_repo import UNDECIDED

# Allow decision that lapsed before the grant froze: the grant is stuck without a new decision.
EXPIRED_ALLOW = """
    g.program_id='welcome' AND g.state='application' AND COALESCE((
        SELECT d.outcome='allow' AND d.expires_at<=clock_timestamp()
        FROM reward_eligibility_decisions d WHERE d.grant_id=g.id ORDER BY d.revision DESC LIMIT 1
    ), false)
"""
WAITING = f"(({UNDECIDED}) OR ({EXPIRED_ALLOW}))"


class WelcomeReviewRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def pending(self, limit: int, only: UUID | None = None) -> list[dict]:
        """Oldest undecided or expired-allow grants on the database clock; `only` picks one."""
        rows = await self.db.execute(
            text(f"""
            SELECT g.id AS grant_id, g.owner_user_id, g.created_at,
                   floor(extract(epoch FROM clock_timestamp() - g.created_at))::bigint AS age,
                   COALESCE((SELECT max(revision) FROM reward_eligibility_decisions d
                             WHERE d.grant_id=g.id), 0) AS revision
            FROM welcome_reward_grants g
            WHERE {WAITING} AND (CAST(:only AS uuid) IS NULL OR g.id = CAST(:only AS uuid))
            ORDER BY g.created_at, g.id LIMIT :limit"""),
            {"limit": limit, "only": only},
        )
        return [dict(row) for row in rows.mappings()]

    async def count(self) -> int:
        """All applications waiting for a decision, expired allows included."""
        return int(
            await self.db.scalar(
                text(f"SELECT count(*) FROM welcome_reward_grants g WHERE {WAITING}")
            )
        )

    async def users(self, ids: Collection[UUID]) -> dict[UUID, User]:
        """Users by id in one query."""
        query = select(User).where(User.id.in_(ids)).execution_options(populate_existing=True)
        return {user.id: user for user in (await self.db.execute(query)).scalars()}

    async def taken_keys(self, keys: Collection[str]) -> dict[str, set[UUID]]:
        """Grants holding each beneficiary key or carrying an allow decision for it."""
        rows = await self.db.execute(
            text("""
            SELECT beneficiary_key, id FROM welcome_reward_grants
              WHERE program_id='welcome' AND beneficiary_key = ANY(CAST(:keys AS text[]))
            UNION
            SELECT beneficiary_key, grant_id FROM reward_eligibility_decisions
              WHERE outcome='allow' AND beneficiary_key = ANY(CAST(:keys AS text[]))"""),
            {"keys": list(keys)},
        )
        found: dict[str, set[UUID]] = {}
        for key, grant_id in rows:
            found.setdefault(key, set()).add(grant_id)
        return found
