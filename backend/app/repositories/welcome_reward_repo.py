"""Welcome reservations share the caller's transaction and stable program lock."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class WelcomeRewardRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

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
