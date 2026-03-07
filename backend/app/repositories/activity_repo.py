"""Activity repository — agent_activity table queries."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_activity_events(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    agent_id: str | None = None,
) -> list[dict]:
    where = "WHERE aa.agent_id = :agent_id" if agent_id else ""
    result = await db.execute(
        text(f"""
            SELECT aa.id, aa.agent_id, aa.action_type, aa.description, aa.created_at,
                   aa.project_id, aa.metadata,
                   a.name as agent_name, a.specialization
            FROM agent_activity aa
            JOIN agents a ON a.id = aa.agent_id
            {where}
            ORDER BY aa.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset, "agent_id": agent_id},
    )
    events = []
    for row in result.mappings():
        events.append({
            "id": str(row["id"]),
            "agent_id": str(row["agent_id"]),
            "agent_name": row["agent_name"],
            "specialization": row["specialization"],
            "action_type": row["action_type"],
            "description": row["description"],
            "project_id": str(row["project_id"]) if row["project_id"] else None,
            "metadata": row["metadata"] or {},
            "ts": str(row["created_at"]),
        })
    return events
