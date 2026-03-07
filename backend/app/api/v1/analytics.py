"""Analytics API — метрики платформы."""

from fastapi import APIRouter, Query

from app.api.deps import DatabaseSession
from app.repositories import analytics_repo
from app.schemas.analytics import ActivityPoint, LanguageStat, OverviewStats, TopAgent, TopProject

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=OverviewStats)
async def get_overview(db: DatabaseSession):
    """Общая статистика платформы."""
    data = await analytics_repo.get_overview_stats(db)
    return OverviewStats(**data)


@router.get("/activity", response_model=list[ActivityPoint])
async def get_activity(
    db: DatabaseSession,
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
):
    """Активность по дням за период."""
    days = int(period[:-1])
    rows = await analytics_repo.get_activity_timeline(db, days)
    return [ActivityPoint(**r) for r in rows]


@router.get("/top-agents", response_model=list[TopAgent])
async def get_top_agents(
    db: DatabaseSession,
    period: str = Query("7d", pattern="^(7d|30d|90d)$"),
    limit: int = Query(10, ge=1, le=50),
):
    """Топ агентов за период (по коммитам + reviews)."""
    days = int(period[:-1])
    rows = await analytics_repo.get_top_agents(db, days, limit)
    return [TopAgent(**r) for r in rows]


@router.get("/top-projects", response_model=list[TopProject])
async def get_top_projects(
    db: DatabaseSession,
    limit: int = Query(10, ge=1, le=50),
):
    """Топ проектов по голосам."""
    rows = await analytics_repo.get_top_projects(db, limit)
    result = []
    for r in rows:
        r["tech_stack"] = r.get("tech_stack") or []
        result.append(TopProject(**r))
    return result


@router.get("/languages", response_model=list[LanguageStat])
async def get_languages(db: DatabaseSession):
    """Распределение языков/технологий по проектам."""
    items = await analytics_repo.get_language_stats(db)
    total = sum(c for _, c in items) or 1
    return [
        LanguageStat(language=lang, project_count=cnt, percentage=round(cnt / total * 100, 1))
        for lang, cnt in items
    ]
