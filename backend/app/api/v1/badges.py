"""Система бейджей для агентов."""

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DatabaseSession
from app.repositories import badge_repo
from app.schemas.badges import AgentBadge, BadgeDefinition

router = APIRouter(tags=["badges"])


# ── Public endpoints ─────────────────────────────────────────────────────────

@router.get("/badges", response_model=list[BadgeDefinition])
async def list_badges(db: DatabaseSession):
    """Все доступные бейджи."""
    rows = await badge_repo.list_badge_definitions(db)
    return [BadgeDefinition(**r) for r in rows]


@router.get("/agents/{agent_id}/badges", response_model=list[AgentBadge])
async def get_agent_badges(agent_id: str, db: DatabaseSession):
    """Бейджи конкретного агента."""
    rows = await badge_repo.get_agent_badges(db, agent_id)
    return [AgentBadge(**r) for r in rows]


# ── Internal utility ─────────────────────────────────────────────────────────

async def award_badges(agent_id: str, db: AsyncSession) -> list[str]:
    """Проверить и выдать новые бейджи агенту. Возвращает список новых badge_id."""
    metrics = await badge_repo.get_agent_metrics(db, agent_id)
    if not metrics:
        return []

    already_awarded = await badge_repo.get_awarded_badge_ids(db, agent_id)
    all_criteria = await badge_repo.get_all_badge_criteria(db)

    newly_awarded: list[str] = []
    for row in all_criteria:
        badge_id = row["id"]
        if badge_id in already_awarded:
            continue
        criteria = row["criteria"]
        metric_val = metrics.get(criteria["metric"], 0) or 0
        if metric_val >= criteria["threshold"]:
            await badge_repo.insert_agent_badge(db, agent_id, badge_id)
            newly_awarded.append(badge_id)

    return newly_awarded
