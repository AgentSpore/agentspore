"""Ownership repository — users wallet, agents owner, project_tokens, project_contributors."""

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


async def verify_agent_api_key(db: AsyncSession, agent_id: str, key_hash: str) -> bool:
    row = await db.execute(
        text("SELECT id FROM agents WHERE id = :aid AND api_key_hash = :kh"),
        {"aid": agent_id, "kh": key_hash},
    )
    return row.scalar_one_or_none() is not None


async def link_agent_to_user(db: AsyncSession, agent_id: str, user_id: str) -> None:
    await db.execute(
        text("UPDATE agents SET owner_user_id = :uid WHERE id = :aid"),
        {"uid": user_id, "aid": agent_id},
    )
    await db.execute(
        text("UPDATE project_contributors SET owner_user_id = :uid WHERE agent_id = :aid"),
        {"uid": user_id, "aid": agent_id},
    )


async def get_project_title(db: AsyncSession, project_id: str) -> str | None:
    row = await db.execute(
        text("SELECT id, title FROM projects WHERE id = :pid"),
        {"pid": project_id},
    )
    first = row.fetchone()
    return first.title if first else None


async def get_project_token_info(db: AsyncSession, project_id: str) -> dict | None:
    row = await db.execute(
        text("""
            SELECT contract_address, chain_id, token_symbol, total_minted
            FROM project_tokens WHERE project_id = :pid
        """),
        {"pid": project_id},
    )
    first = row.fetchone()
    if not first:
        return None
    return {
        "contract_address": first.contract_address,
        "chain_id": first.chain_id,
        "token_symbol": first.token_symbol,
        "total_minted": first.total_minted,
    }


async def get_contributor_shares(db: AsyncSession, project_id: str) -> list:
    rows = await db.execute(
        text("""
            SELECT
                pc.agent_id, a.name AS agent_name,
                pc.owner_user_id, u.name AS owner_name, u.wallet_address,
                pc.contribution_points, pc.share_pct, pc.tokens_minted
            FROM project_contributors pc
            JOIN agents a ON a.id = pc.agent_id
            LEFT JOIN users u ON u.id = pc.owner_user_id
            WHERE pc.project_id = :pid
            ORDER BY pc.contribution_points DESC
        """),
        {"pid": project_id},
    )
    return rows.fetchall()


async def get_user_token_holdings(db: AsyncSession, user_id: str) -> list:
    rows = await db.execute(
        text("""
            SELECT
                p.id AS project_id, p.title AS project_title,
                pt.contract_address, pt.token_symbol,
                pc.tokens_minted
            FROM project_contributors pc
            JOIN projects p ON p.id = pc.project_id
            JOIN project_tokens pt ON pt.project_id = p.id
            WHERE pc.owner_user_id = :uid
            ORDER BY pc.tokens_minted DESC
        """),
        {"uid": user_id},
    )
    return rows.fetchall()
