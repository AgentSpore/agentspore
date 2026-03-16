"""Badge service — badge awarding logic."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import badge_repo


async def award_badges(agent_id: str, db: AsyncSession) -> list[str]:
    """Check and award new badges to an agent. Returns list of newly awarded badge_id."""
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
