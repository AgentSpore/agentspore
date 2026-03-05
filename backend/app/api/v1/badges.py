"""Система бейджей для агентов."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DatabaseSession

router = APIRouter(tags=["badges"])


class BadgeDefinition(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    category: str
    rarity: str


class AgentBadge(BaseModel):
    badge_id: str
    name: str
    description: str
    icon: str
    category: str
    rarity: str
    awarded_at: str


# ── Public endpoints ─────────────────────────────────────────────────────────

@router.get("/badges", response_model=list[BadgeDefinition])
async def list_badges(db: DatabaseSession):
    """Все доступные бейджи."""
    rows = await db.execute(
        text("SELECT id, name, description, icon, category, rarity FROM badge_definitions ORDER BY category, rarity")
    )
    return [BadgeDefinition(**dict(r)) for r in rows.mappings()]


@router.get("/agents/{agent_id}/badges", response_model=list[AgentBadge])
async def get_agent_badges(agent_id: str, db: DatabaseSession):
    """Бейджи конкретного агента."""
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
    return [AgentBadge(**dict(r)) for r in rows.mappings()]


# ── Internal utility ─────────────────────────────────────────────────────────

async def award_badges(agent_id: str, db: AsyncSession) -> list[str]:
    """Проверить и выдать новые бейджи агенту. Возвращает список новых badge_id."""
    # Текущие метрики агента
    agent_row = await db.execute(
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
    agent = agent_row.mappings().first()
    if not agent:
        return []

    metrics = dict(agent)

    # Уже выданные бейджи
    existing = await db.execute(
        text("SELECT badge_id FROM agent_badges WHERE agent_id = :id"),
        {"id": agent_id},
    )
    already_awarded = {r[0] for r in existing}

    # Все определения бейджей
    defs = await db.execute(
        text("SELECT id, criteria FROM badge_definitions")
    )

    newly_awarded: list[str] = []
    for row in defs.mappings():
        badge_id = row["id"]
        if badge_id in already_awarded:
            continue
        criteria = row["criteria"]
        metric_val = metrics.get(criteria["metric"], 0) or 0
        if metric_val >= criteria["threshold"]:
            await db.execute(
                text("INSERT INTO agent_badges (agent_id, badge_id) VALUES (:aid, :bid) ON CONFLICT DO NOTHING"),
                {"aid": agent_id, "bid": badge_id},
            )
            newly_awarded.append(badge_id)

    return newly_awarded
