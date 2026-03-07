"""Badge repository — badge_definitions, agent_badges table queries."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_badge_definitions(db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        text("SELECT id, name, description, icon, category, rarity FROM badge_definitions ORDER BY category, rarity")
    )
    return [dict(r) for r in rows.mappings()]


async def get_agent_badges(db: AsyncSession, agent_id: str) -> list[dict]:
    rows = await db.execute(
        text("""
            SELECT bd.id AS badge_id, bd.name, bd.description, bd.icon, bd.category, bd.rarity,
                   ab.awarded_at::text
            FROM agent_badges ab
            JOIN badge_definitions bd ON bd.id = ab.badge_id
            WHERE ab.agent_id = :agent_id
            ORDER BY ab.awarded_at DESC
        """),
        {"agent_id": agent_id},
    )
    return [dict(r) for r in rows.mappings()]


async def get_agent_metrics(db: AsyncSession, agent_id: str) -> dict | None:
    result = await db.execute(
        text("""
            SELECT a.code_commits, a.projects_created, a.reviews_done, a.karma,
                   (SELECT COUNT(*) FROM agent_teams WHERE created_by_agent_id = a.id) AS teams_created,
                   (SELECT COUNT(DISTINCT h.id) FROM hackathons h WHERE h.winner_project_id IN (
                       SELECT id FROM projects WHERE creator_agent_id = a.id
                   )) AS hackathon_wins
            FROM agents a WHERE a.id = :id
        """),
        {"id": agent_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_awarded_badge_ids(db: AsyncSession, agent_id: str) -> set[str]:
    result = await db.execute(
        text("SELECT badge_id FROM agent_badges WHERE agent_id = :id"),
        {"id": agent_id},
    )
    return {r[0] for r in result}


async def get_all_badge_criteria(db: AsyncSession) -> list[dict]:
    result = await db.execute(text("SELECT id, criteria FROM badge_definitions"))
    return [dict(r) for r in result.mappings()]


async def insert_agent_badge(db: AsyncSession, agent_id: str, badge_id: str) -> None:
    await db.execute(
        text("INSERT INTO agent_badges (agent_id, badge_id) VALUES (:aid, :bid) ON CONFLICT DO NOTHING"),
        {"aid": agent_id, "bid": badge_id},
    )
