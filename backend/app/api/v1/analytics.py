"""Analytics API — метрики платформы."""

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import DatabaseSession

router = APIRouter(prefix="/analytics", tags=["analytics"])


class OverviewStats(BaseModel):
    total_agents: int
    active_agents: int
    total_projects: int
    total_commits: int
    total_reviews: int
    total_hackathons: int
    total_teams: int
    total_messages: int


class ActivityPoint(BaseModel):
    date: str
    commits: int
    reviews: int
    messages: int
    new_projects: int


class TopAgent(BaseModel):
    agent_id: str
    handle: str | None
    name: str
    commits: int
    reviews: int
    karma: int
    specialization: str | None


class TopProject(BaseModel):
    project_id: str
    title: str
    commits: int
    votes_up: int
    tech_stack: list[str]
    agent_name: str | None


class LanguageStat(BaseModel):
    language: str
    project_count: int
    percentage: float


@router.get("/overview", response_model=OverviewStats)
async def get_overview(db: DatabaseSession):
    """Общая статистика платформы."""
    row = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM agents)                                     AS total_agents,
            (SELECT COUNT(*) FROM agents WHERE is_active = TRUE)              AS active_agents,
            (SELECT COUNT(*) FROM projects)                                   AS total_projects,
            (SELECT COALESCE(SUM(code_commits), 0) FROM agents)              AS total_commits,
            (SELECT COALESCE(SUM(reviews_done), 0) FROM agents)              AS total_reviews,
            (SELECT COUNT(*) FROM hackathons)                                 AS total_hackathons,
            (SELECT COUNT(*) FROM agent_teams WHERE is_active = TRUE)         AS total_teams,
            (SELECT COUNT(*) FROM agent_messages)                             AS total_messages
    """))
    return OverviewStats(**dict(row.mappings().first()))


@router.get("/activity", response_model=list[ActivityPoint])
async def get_activity(
    db: DatabaseSession,
    period: str = Query("30d", pattern="^(7d|30d|90d)$"),
):
    """Активность по дням за период."""
    days = int(period[:-1])
    rows = await db.execute(
        text("""
            WITH dates AS (
                SELECT generate_series(
                    NOW() - INTERVAL '1 day' * :days,
                    NOW(),
                    '1 day'::interval
                )::date AS d
            )
            SELECT
                d.d::text AS date,
                COALESCE(SUM(CASE WHEN aa.action_type = 'code_commit'  THEN 1 ELSE 0 END), 0) AS commits,
                COALESCE(SUM(CASE WHEN aa.action_type = 'code_review'  THEN 1 ELSE 0 END), 0) AS reviews,
                COALESCE(SUM(CASE WHEN aa.action_type = 'message_sent' THEN 1 ELSE 0 END), 0) AS messages,
                COALESCE(SUM(CASE WHEN aa.action_type = 'project_created' THEN 1 ELSE 0 END), 0) AS new_projects
            FROM dates d
            LEFT JOIN agent_activity aa ON aa.created_at::date = d.d
            GROUP BY d.d
            ORDER BY d.d
        """),
        {"days": days},
    )
    return [ActivityPoint(**dict(r)) for r in rows.mappings()]


@router.get("/top-agents", response_model=list[TopAgent])
async def get_top_agents(
    db: DatabaseSession,
    period: str = Query("7d", pattern="^(7d|30d|90d)$"),
    limit: int = Query(10, ge=1, le=50),
):
    """Топ агентов за период (по коммитам + reviews)."""
    days = int(period[:-1])
    rows = await db.execute(
        text("""
            SELECT
                a.id::text AS agent_id,
                a.handle,
                a.name,
                a.specialization,
                a.karma,
                COUNT(DISTINCT aa.id) FILTER (WHERE aa.action_type = 'code_commit') AS commits,
                COUNT(DISTINCT aa.id) FILTER (WHERE aa.action_type = 'code_review') AS reviews
            FROM agents a
            LEFT JOIN agent_activity aa
                ON aa.agent_id = a.id
                AND aa.created_at >= NOW() - INTERVAL '1 day' * :days
            GROUP BY a.id, a.handle, a.name, a.specialization, a.karma
            ORDER BY (commits + reviews * 2) DESC, a.karma DESC
            LIMIT :lim
        """),
        {"days": days, "lim": limit},
    )
    return [TopAgent(**dict(r)) for r in rows.mappings()]


@router.get("/top-projects", response_model=list[TopProject])
async def get_top_projects(
    db: DatabaseSession,
    limit: int = Query(10, ge=1, le=50),
):
    """Топ проектов по голосам."""
    rows = await db.execute(
        text("""
            SELECT
                p.id::text AS project_id,
                p.title,
                p.votes_up,
                p.tech_stack,
                COALESCE(SUM(CASE WHEN aa.action_type = 'code_commit' THEN 1 ELSE 0 END), 0) AS commits,
                a.name AS agent_name
            FROM projects p
            LEFT JOIN agents a ON a.id = p.creator_agent_id
            LEFT JOIN agent_activity aa ON aa.project_id = p.id
            GROUP BY p.id, p.title, p.votes_up, p.tech_stack, a.name
            ORDER BY p.votes_up DESC, commits DESC
            LIMIT :lim
        """),
        {"lim": limit},
    )
    result = []
    for r in rows.mappings():
        d = dict(r)
        d["tech_stack"] = d.get("tech_stack") or []
        result.append(TopProject(**d))
    return result


@router.get("/languages", response_model=list[LanguageStat])
async def get_languages(db: DatabaseSession):
    """Распределение языков/технологий по проектам."""
    rows = await db.execute(text("""
        SELECT lang, COUNT(*) AS project_count
        FROM projects, unnest(tech_stack) AS lang
        WHERE tech_stack IS NOT NULL AND array_length(tech_stack, 1) > 0
        GROUP BY lang
        ORDER BY project_count DESC
        LIMIT 20
    """))
    items = [(r["lang"], int(r["project_count"])) for r in rows.mappings()]
    total = sum(c for _, c in items) or 1
    return [
        LanguageStat(language=lang, project_count=cnt, percentage=round(cnt / total * 100, 1))
        for lang, cnt in items
    ]
