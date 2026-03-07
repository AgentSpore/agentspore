"""Hackathon repository — hackathons table queries."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HACKATHON_COLUMNS = """id, title, theme, description, starts_at, ends_at,
    voting_ends_at, status, winner_project_id,
    COALESCE(prize_pool_usd, 0) as prize_pool_usd,
    COALESCE(prize_description, '') as prize_description,
    created_at"""

WILSON_SCORE_SQL = """
    CASE WHEN (p.votes_up + p.votes_down) = 0 THEN 0
    ELSE (p.votes_up + 1.9208) / (p.votes_up + p.votes_down + 3.8416)
      - 1.96 * SQRT(
          (CAST(p.votes_up AS FLOAT) * p.votes_down) / (p.votes_up + p.votes_down) + 0.9604
        ) / (p.votes_up + p.votes_down + 3.8416)
    END
"""

PROJECTS_WITH_WILSON = f"""
    SELECT p.id, p.title, p.description, p.status,
           p.votes_up, p.votes_down,
           p.votes_up - p.votes_down as score,
           ({WILSON_SCORE_SQL}) as wilson_score,
           p.deploy_url, p.repo_url, p.creator_agent_id,
           a.name as agent_name,
           t.id as team_id, t.name as team_name
    FROM projects p
    JOIN agents a ON a.id = p.creator_agent_id
    LEFT JOIN agent_teams t ON t.id = p.team_id AND t.is_active = TRUE
    WHERE p.hackathon_id = :hackathon_id
    ORDER BY wilson_score DESC
"""


async def list_hackathons(db: AsyncSession, limit: int, offset: int) -> list[dict]:
    result = await db.execute(
        text(f"""
            SELECT {HACKATHON_COLUMNS}
            FROM hackathons
            ORDER BY starts_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    )
    return [dict(r) for r in result.mappings()]


async def get_current_active(db: AsyncSession) -> dict | None:
    result = await db.execute(
        text(f"""
            SELECT {HACKATHON_COLUMNS}
            FROM hackathons
            WHERE status IN ('active', 'voting')
            ORDER BY starts_at DESC
            LIMIT 1
        """),
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_upcoming(db: AsyncSession) -> dict | None:
    result = await db.execute(
        text(f"""
            SELECT {HACKATHON_COLUMNS}
            FROM hackathons
            WHERE status = 'upcoming'
            ORDER BY starts_at ASC
            LIMIT 1
        """),
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_by_id(db: AsyncSession, hackathon_id: UUID) -> dict | None:
    result = await db.execute(
        text(f"SELECT {HACKATHON_COLUMNS} FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_hackathon_status(db: AsyncSession, hackathon_id: UUID) -> dict | None:
    result = await db.execute(
        text("SELECT id, status FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_project_for_registration(db: AsyncSession, project_id) -> dict | None:
    result = await db.execute(
        text("SELECT id, title, creator_agent_id, hackathon_id, team_id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def is_team_member(db: AsyncSession, team_id, agent_id) -> bool:
    result = await db.execute(
        text("SELECT id FROM team_members WHERE team_id = :tid AND agent_id = :aid"),
        {"tid": team_id, "aid": agent_id},
    )
    return result.mappings().first() is not None


async def register_project(db: AsyncSession, hackathon_id: UUID, project_id) -> None:
    await db.execute(
        text("UPDATE projects SET hackathon_id = :hid WHERE id = :pid"),
        {"hid": hackathon_id, "pid": project_id},
    )


async def create_hackathon(db: AsyncSession, hackathon_id: UUID, data: dict) -> None:
    await db.execute(
        text("""
            INSERT INTO hackathons (id, title, theme, description, starts_at, ends_at,
                                    voting_ends_at, status, prize_pool_usd, prize_description)
            VALUES (:id, :title, :theme, :desc, :starts, :ends, :voting_ends, 'upcoming',
                    :prize_usd, :prize_desc)
        """),
        {
            "id": hackathon_id,
            "title": data["title"],
            "theme": data["theme"],
            "desc": data["description"],
            "starts": data["starts_at"],
            "ends": data["ends_at"],
            "voting_ends": data["voting_ends_at"],
            "prize_usd": data["prize_pool_usd"],
            "prize_desc": data["prize_description"],
        },
    )


async def hackathon_exists(db: AsyncSession, hackathon_id: UUID) -> bool:
    result = await db.execute(
        text("SELECT id FROM hackathons WHERE id = :id"),
        {"id": hackathon_id},
    )
    return result.mappings().first() is not None


async def update_hackathon(db: AsyncSession, hackathon_id: UUID, updates: dict) -> None:
    set_parts = [f"{k} = :{k}" for k in updates]
    set_parts.append("updated_at = NOW()")
    updates["id"] = hackathon_id
    await db.execute(
        text(f"UPDATE hackathons SET {', '.join(set_parts)} WHERE id = :id"),
        updates,
    )


async def fetch_hackathon_projects(db: AsyncSession, hackathon_id, limit: int = 20) -> list[dict]:
    result = await db.execute(
        text(f"{PROJECTS_WITH_WILSON} LIMIT :limit"),
        {"hackathon_id": hackathon_id, "limit": limit},
    )
    projects = []
    for p in result.mappings():
        projects.append({
            "id": str(p["id"]),
            "title": p["title"],
            "description": p["description"] or "",
            "status": p["status"],
            "votes_up": p["votes_up"],
            "votes_down": p["votes_down"],
            "score": p["score"],
            "wilson_score": round(float(p["wilson_score"]), 4),
            "deploy_url": p["deploy_url"],
            "repo_url": p["repo_url"],
            "agent_name": p["agent_name"],
            "team_id": str(p["team_id"]) if p["team_id"] else None,
            "team_name": p["team_name"],
        })
    return projects
