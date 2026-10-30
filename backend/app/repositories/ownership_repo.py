"""Ownership repository — users wallet, agents owner, project_contributors."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_wallet_uniqueness(db: AsyncSession, wallet_address: str, user_id: str) -> bool:
    row = await db.execute(
        text("SELECT id FROM users WHERE wallet_address = :w AND id != :uid"),
        {"w": wallet_address.lower(), "uid": user_id},
    )
    return row.scalar_one_or_none() is not None


async def update_user_wallet(db: AsyncSession, user_id: str, wallet_address: str) -> None:
    await db.execute(
        text("""
            UPDATE users
            SET wallet_address = :w, wallet_connected_at = NOW(), updated_at = NOW()
            WHERE id = :uid
        """),
        {"w": wallet_address.lower(), "uid": user_id},
    )


async def get_project_title(db: AsyncSession, project_id: str) -> str | None:
    row = await db.execute(
        text("SELECT id, title FROM projects WHERE id = :pid"),
        {"pid": project_id},
    )
    first = row.fetchone()
    return first.title if first else None


async def get_contributor_shares(db: AsyncSession, project_id: str) -> list:
    rows = await db.execute(
        text("""
            SELECT
                pc.agent_id, a.name AS agent_name,
                pc.owner_user_id, u.name AS owner_name, u.wallet_address,
                pc.contribution_points, pc.share_pct
            FROM project_contributors pc
            JOIN agents a ON a.id = pc.agent_id
            LEFT JOIN users u ON u.id = pc.owner_user_id
            WHERE pc.project_id = :pid
            ORDER BY pc.contribution_points DESC
        """),
        {"pid": project_id},
    )
    return rows.fetchall()


class AgentOwnershipRepository:
    """Keep all claim operations in the request-owned PostgreSQL transaction."""

    claim_query = """
        SELECT c.id AS claim_id,c.agent_id,a.name AS agent_name,c.user_id,u.name AS user_name,
               CASE WHEN c.status='pending' AND c.expires_at<=clock_timestamp()
                    THEN 'expired' ELSE c.status END AS status,
               c.expires_at,c.agent_key_hash_snapshot
        FROM agent_owner_claims c JOIN agents a ON a.id=c.agent_id JOIN users u ON u.id=c.user_id
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_agent(self, agent_id: UUID) -> dict | None:
        """Serialize mutations on the intended agent, including rotation races."""
        result = await self.db.execute(
            text(
                "SELECT id,name,is_active,api_key_hash,owner_user_id "
                "FROM agents WHERE id=:id FOR UPDATE"
            ),
            {"id": agent_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def lock_user(self, user_id: UUID) -> dict | None:
        """Serialize user-wide admission and recheck verified identity."""
        result = await self.db.execute(
            text("SELECT id,name,is_verified FROM users WHERE id=:id FOR UPDATE"), {"id": user_id}
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def claim(self, claim_id: UUID) -> dict | None:
        """Read one intent with expiry derived from the current database clock."""
        row = (
            (await self.db.execute(text(self.claim_query + " WHERE c.id=:id"), {"id": claim_id}))
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def find(self, agent_id: UUID, user_id: UUID, status: str) -> dict | None:
        """Find the latest stored lifecycle state for the exact agent and user."""
        result = await self.db.execute(
            text(
                self.claim_query + " WHERE c.agent_id=:a AND c.user_id=:u AND c.status=:s "
                "ORDER BY c.created_at DESC LIMIT 1"
            ),
            {"a": agent_id, "u": user_id, "s": status},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def counts(self, user_id: UUID) -> tuple[int, int]:
        """Count live and recent intents for the locked verified human."""
        result = await self.db.execute(
            text("""
            SELECT (SELECT count(*) FROM agent_owner_claims WHERE user_id=:u
                    AND status='pending' AND expires_at>clock_timestamp()),
                   (SELECT count(*) FROM agent_owner_claims WHERE user_id=:u
                    AND created_at>clock_timestamp()-INTERVAL '1 hour')
        """),
            {"u": user_id},
        )
        pending, recent = result.one()
        return pending, recent

    async def create(self, agent_id: UUID, user_id: UUID, key_hash: str) -> UUID:
        """Bind immutable identities and the current credential generation."""
        return await self.db.scalar(
            text("""
            INSERT INTO agent_owner_claims(agent_id,user_id,agent_key_hash_snapshot)
            VALUES (:a,:u,:h) RETURNING id
        """),
            {"a": agent_id, "u": user_id, "h": key_hash},
        )

    async def status(self, claim_id: UUID, status: str) -> bool:
        """Consume only a pending intent before its actual deadline."""
        result = await self.db.execute(
            text("""
            UPDATE agent_owner_claims SET status=:s,decided_at=clock_timestamp()
            WHERE id=:id AND status='pending'
              AND (:s IN ('canceled','expired') OR expires_at>clock_timestamp()) RETURNING id
        """),
            {"id": claim_id, "s": status},
        )
        return result.scalar_one_or_none() is not None

    async def assign(self, agent_id: UUID, user_id: UUID, key_hash: str) -> bool:
        """Assign only an unowned active agent and preserve historical contributors."""
        result = await self.db.execute(
            text("""
            UPDATE agents SET owner_user_id=:u WHERE id=:a AND owner_user_id IS NULL
                AND is_active=TRUE AND api_key_hash=:h RETURNING id
        """),
            {"a": agent_id, "u": user_id, "h": key_hash},
        )
        if result.scalar_one_or_none() is None:
            return False
        await self.db.execute(
            text("""
            UPDATE project_contributors SET owner_user_id=:u
            WHERE agent_id=:a AND owner_user_id IS NULL
        """),
            {"a": agent_id, "u": user_id},
        )
        return True
