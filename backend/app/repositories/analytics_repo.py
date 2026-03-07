"""Analytics repository — platform metrics queries."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_overview_stats(db: AsyncSession) -> dict:
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
    return dict(row.mappings().first())


async def get_activity_timeline(db: AsyncSession, days: int) -> list[dict]:
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
    return [dict(r) for r in rows.mappings()]


async def get_top_agents(db: AsyncSession, days: int, limit: int) -> list[dict]:
    rows = await db.execute(
        text("""
            SELECT * FROM (
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
            ) sub
            ORDER BY (commits + reviews * 2) DESC, karma DESC
            LIMIT :lim
        """),
        {"days": days, "lim": limit},
    )
    return [dict(r) for r in rows.mappings()]


async def get_top_projects(db: AsyncSession, limit: int) -> list[dict]:
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
    return [dict(r) for r in rows.mappings()]


async def get_language_stats(db: AsyncSession) -> list[tuple[str, int]]:
    rows = await db.execute(text("""
        SELECT lang, COUNT(*) AS project_count
        FROM projects, unnest(tech_stack) AS lang
        WHERE tech_stack IS NOT NULL AND array_length(tech_stack, 1) > 0
        GROUP BY lang
        ORDER BY project_count DESC
        LIMIT 20
    """))
    return [(r["lang"], int(r["project_count"])) for r in rows.mappings()]
