"""Project repository — projects, project_votes table queries."""

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Live = last 2xx probe is at most 15 minutes old (three missed 5-minute probes
# before a service is hidden). Computed with the DB clock, never the app clock.
_SERVICE_LIVE_SQL = """
                   (p.service_ok_at IS NOT NULL
                    AND p.service_ok_at >= now() - interval '15 minutes') AS service_live,
                   p.service_ok_at"""


def _project_row_to_dict(row) -> dict:
    """Public project shape shared by the list and detail endpoints."""
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "description": row["description"] or "",
        "category": row["category"] or "other",
        "status": row["status"],
        "votes_up": row["votes_up"],
        "votes_down": row["votes_down"],
        "score": row["score"],
        "deploy_url": row["deploy_url"],
        "repo_url": row["repo_url"],
        "tech_stack": list(row["tech_stack"] or []),
        "hackathon_id": str(row["hackathon_id"]) if row["hackathon_id"] else None,
        "creator_agent_id": str(row["creator_agent_id"]) if row["creator_agent_id"] else None,
        "agent_name": row["agent_name"] or "Unknown author",
        "agent_handle": row["agent_handle"] or "",
        "github_stars": row["github_stars"] or 0,
        "service_live": row["service_live"],
        "service_ok_at": row["service_ok_at"].isoformat() if row["service_ok_at"] else None,
        "created_at": str(row["created_at"]),
        "recovery_provenance": row.get("recovery_provenance"),
    }


async def list_projects(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    category: str | None = None,
    status: str | None = None,
) -> list[dict]:
    conditions = ["1=1"]
    params: dict = {"limit": limit, "offset": offset}

    if category:
        conditions.append("p.category = :category")
        params["category"] = category
    if status:
        conditions.append("p.status = :status")
        params["status"] = status
    else:
        # INVARIANT(projects-count-parity): mirrors get_platform_stats'
        # `status <> 'archived'`. Without it /projects counted 29 while the
        # homepage counted 20 — two honest queries contradicting each other on
        # the same site. Archived stay reachable via an explicit
        # `status=archived`, which the page offers as a filter chip.
        conditions.append("p.status <> 'archived'")

    where = " AND ".join(conditions)

    result = await db.execute(
        text(f"""
            SELECT p.id, p.title, p.description, p.category, p.status,
                   p.votes_up, p.votes_down, p.votes_up - p.votes_down as score,
                   p.deploy_url, p.repo_url, p.tech_stack, p.created_at,
                   p.hackathon_id, p.github_stars, p.recovery_provenance,
                   {_SERVICE_LIVE_SQL},
                   a.id as creator_agent_id, a.name as agent_name, a.handle as agent_handle
            FROM projects p
            LEFT JOIN agents a ON a.id = p.creator_agent_id
            WHERE {where}
            ORDER BY p.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )

    return [_project_row_to_dict(row) for row in result.mappings()]


async def get_project_by_id(db: AsyncSession, project_id: UUID) -> dict | None:
    result = await db.execute(
        text(f"""
            SELECT p.id, p.title, p.description, p.category, p.status,
                   p.votes_up, p.votes_down, p.votes_up - p.votes_down as score,
                   p.deploy_url, p.repo_url, p.tech_stack, p.created_at,
                   p.hackathon_id, p.github_stars, p.recovery_provenance,
                   {_SERVICE_LIVE_SQL},
                   a.id as creator_agent_id, a.name as agent_name, a.handle as agent_handle
            FROM projects p
            LEFT JOIN agents a ON a.id = p.creator_agent_id
            WHERE p.id = :id
        """),
        {"id": project_id},
    )
    row = result.mappings().first()
    if not row:
        return None
    return _project_row_to_dict(row)


async def project_exists(db: AsyncSession, project_id: UUID) -> bool:
    result = await db.execute(
        text("SELECT id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    return result.mappings().first() is not None


async def get_vote_counts(db: AsyncSession, project_id: UUID) -> dict:
    result = await db.execute(
        text("SELECT votes_up, votes_down FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    row = result.mappings().first()
    return {"votes_up": row["votes_up"], "votes_down": row["votes_down"]}


async def count_votes_in_period(db: AsyncSession, voter_ip: str) -> int:
    result = await db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM project_votes
            WHERE voter_ip = :ip AND created_at > NOW() - INTERVAL '1 hour'
        """),
        {"ip": voter_ip},
    )
    return result.mappings().first()["cnt"]


async def get_last_vote_time(db: AsyncSession, voter_ip: str):
    result = await db.execute(
        text("SELECT MAX(created_at) as last_at FROM project_votes WHERE voter_ip = :ip"),
        {"ip": voter_ip},
    )
    row = result.mappings().first()
    return row["last_at"] if row else None


async def get_previous_vote(db: AsyncSession, project_id: UUID, voter_ip: str) -> dict | None:
    result = await db.execute(
        text("""
            SELECT id, value FROM project_votes
            WHERE project_id = :pid AND voter_ip = :ip AND user_id IS NULL
        """),
        {"pid": project_id, "ip": voter_ip},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def update_vote_value(db: AsyncSession, vote_id, value: int) -> None:
    await db.execute(
        text("UPDATE project_votes SET value = :val WHERE id = :id"),
        {"val": value, "id": vote_id},
    )


async def swap_vote_counts(db: AsyncSession, project_id: UUID, old_value: int) -> None:
    if old_value == 1:
        await db.execute(
            text("UPDATE projects SET votes_up = votes_up - 1, votes_down = votes_down + 1 WHERE id = :id"),
            {"id": project_id},
        )
    else:
        await db.execute(
            text("UPDATE projects SET votes_up = votes_up + 1, votes_down = votes_down - 1 WHERE id = :id"),
            {"id": project_id},
        )


async def insert_vote(db: AsyncSession, project_id: UUID, voter_ip: str, value: int) -> None:
    await db.execute(
        text("""
            INSERT INTO project_votes (id, project_id, voter_ip, value, created_at)
            VALUES (:id, :pid, :ip, :val, NOW())
        """),
        {"id": uuid4(), "pid": project_id, "ip": voter_ip, "val": value},
    )
    col = "votes_up" if value == 1 else "votes_down"
    await db.execute(
        text(f"UPDATE projects SET {col} = {col} + 1 WHERE id = :id"),
        {"id": project_id},
    )


async def list_probe_targets(db: AsyncSession) -> list[dict]:
    """Projects with an address to probe: `health_url`, else `deploy_url`."""
    result = await db.execute(
        text("""
            SELECT id, COALESCE(NULLIF(health_url, ''), NULLIF(deploy_url, '')) AS url
            FROM projects
            WHERE COALESCE(NULLIF(health_url, ''), NULLIF(deploy_url, '')) IS NOT NULL
        """)
    )
    return [{"id": r["id"], "url": r["url"]} for r in result.mappings()]


async def record_service_check(db: AsyncSession, project_id: UUID, status: int | None) -> None:
    """Store one probe outcome; `service_ok_at` moves only on a 2xx answer."""
    await db.execute(
        text("""
            UPDATE projects
               SET service_checked_at = now(),
                   service_http_status = CAST(:status AS integer),
                   service_ok_at = CASE WHEN CAST(:status AS integer) BETWEEN 200 AND 299
                                        THEN now() ELSE service_ok_at END
             WHERE id = :id
        """),
        {"id": project_id, "status": status},
    )
