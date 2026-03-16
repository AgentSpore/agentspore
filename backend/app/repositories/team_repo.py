"""Team repository — agent_teams, team_members, team_messages, projects queries."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Auth helpers ──

async def get_agent_by_api_key_hash(db: AsyncSession, key_hash: str) -> dict | None:
    result = await db.execute(
        text("SELECT id, name, specialization FROM agents WHERE api_key_hash = :h AND is_active = TRUE"),
        {"h": key_hash},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_membership(db: AsyncSession, team_id: UUID, identity_type: str, identity_id) -> dict | None:
    if identity_type == "agent":
        result = await db.execute(
            text("SELECT id, role FROM team_members WHERE team_id = :tid AND agent_id = :aid"),
            {"tid": team_id, "aid": identity_id},
        )
    else:
        result = await db.execute(
            text("SELECT id, role FROM team_members WHERE team_id = :tid AND user_id = :uid"),
            {"tid": team_id, "uid": identity_id},
        )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_active_team(db: AsyncSession, team_id: UUID) -> dict | None:
    result = await db.execute(
        text("SELECT id, name, is_active FROM agent_teams WHERE id = :id"),
        {"id": team_id},
    )
    row = result.mappings().first()
    if not row or not row["is_active"]:
        return None
    return dict(row)


# ── Teams CRUD ──

async def create_team(db: AsyncSession, name: str, description: str, agent_id, user_id) -> dict:
    result = await db.execute(
        text("""
            INSERT INTO agent_teams (name, description, created_by_agent_id, created_by_user_id)
            VALUES (:name, :desc, :agent_id, :user_id)
            RETURNING id, name, description, created_at
        """),
        {"name": name, "desc": description, "agent_id": agent_id, "user_id": user_id},
    )
    return dict(result.mappings().first())


async def add_owner_member(db: AsyncSession, team_id, agent_id, user_id) -> None:
    await db.execute(
        text("INSERT INTO team_members (team_id, agent_id, user_id, role) VALUES (:tid, :aid, :uid, 'owner')"),
        {"tid": team_id, "aid": agent_id, "uid": user_id},
    )


async def list_teams(db: AsyncSession, limit: int, offset: int) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT t.id, t.name, t.description, t.avatar_url, t.created_at,
                   t.created_by_agent_id, t.created_by_user_id,
                   COALESCE(a.name, u.name) as creator_name,
                   (SELECT COUNT(*) FROM team_members tm WHERE tm.team_id = t.id) as member_count,
                   (SELECT COUNT(*) FROM projects p WHERE p.team_id = t.id) as project_count
            FROM agent_teams t
            LEFT JOIN agents a ON a.id = t.created_by_agent_id
            LEFT JOIN users u ON u.id = t.created_by_user_id
            WHERE t.is_active = TRUE
            ORDER BY t.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": limit, "offset": offset},
    )
    return [dict(r) for r in result.mappings()]


async def get_team_detail(db: AsyncSession, team_id: UUID) -> dict | None:
    result = await db.execute(
        text("""
            SELECT t.id, t.name, t.description, t.avatar_url, t.created_at,
                   t.created_by_agent_id, t.created_by_user_id,
                   COALESCE(a.name, u.name) as creator_name
            FROM agent_teams t
            LEFT JOIN agents a ON a.id = t.created_by_agent_id
            LEFT JOIN users u ON u.id = t.created_by_user_id
            WHERE t.id = :id AND t.is_active = TRUE
        """),
        {"id": team_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def update_team(db: AsyncSession, team_id: UUID, updates: dict) -> None:
    set_parts = [f"{k} = :{k}" for k in updates]
    set_parts.append("updated_at = NOW()")
    updates["id"] = team_id
    await db.execute(
        text(f"UPDATE agent_teams SET {', '.join(set_parts)} WHERE id = :id"),
        updates,
    )


async def soft_delete_team(db: AsyncSession, team_id: UUID) -> None:
    await db.execute(text("UPDATE projects SET team_id = NULL WHERE team_id = :tid"), {"tid": team_id})
    await db.execute(
        text("UPDATE agent_teams SET is_active = FALSE, updated_at = NOW() WHERE id = :id"),
        {"id": team_id},
    )


# ── Members ──

async def get_team_members(db: AsyncSession, team_id: UUID) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT tm.id, tm.agent_id, tm.user_id, tm.role, tm.joined_at,
                   COALESCE(a.name, u.name) as name,
                   a.handle as handle,
                   CASE WHEN tm.agent_id IS NOT NULL THEN 'agent' ELSE 'user' END as member_type
            FROM team_members tm
            LEFT JOIN agents a ON a.id = tm.agent_id
            LEFT JOIN users u ON u.id = tm.user_id
            WHERE tm.team_id = :tid
            ORDER BY tm.role DESC, tm.joined_at ASC
        """),
        {"tid": team_id},
    )
    return [dict(r) for r in result.mappings()]


async def validate_agent(db: AsyncSession, agent_id: str) -> bool:
    result = await db.execute(
        text("SELECT id FROM agents WHERE id = :id AND is_active = TRUE"),
        {"id": agent_id},
    )
    return result.mappings().first() is not None


async def validate_user(db: AsyncSession, user_id: str) -> bool:
    result = await db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
    return result.mappings().first() is not None


async def member_exists(db: AsyncSession, team_id: UUID, agent_id: str | None, user_id: str | None) -> bool:
    result = await db.execute(
        text("SELECT id FROM team_members WHERE team_id = :tid AND (agent_id = :aid OR user_id = :uid)"),
        {"tid": team_id, "aid": agent_id, "uid": user_id},
    )
    return result.mappings().first() is not None


async def add_member(db: AsyncSession, team_id: UUID, agent_id, user_id, role: str) -> dict:
    result = await db.execute(
        text("""
            INSERT INTO team_members (team_id, agent_id, user_id, role)
            VALUES (:tid, :aid, :uid, :role)
            RETURNING id, joined_at
        """),
        {"tid": team_id, "aid": agent_id, "uid": user_id, "role": role},
    )
    return dict(result.mappings().first())


async def auto_add_agent_owner(db: AsyncSession, team_id: UUID, agent_id) -> bool:
    """If agent has an owner_user_id, auto-add that user as team member. Returns True if added."""
    result = await db.execute(
        text("SELECT owner_user_id FROM agents WHERE id = :aid"),
        {"aid": agent_id},
    )
    row = result.mappings().first()
    if not row or not row["owner_user_id"]:
        return False
    user_id = str(row["owner_user_id"])
    if await member_exists(db, team_id, None, user_id):
        return False
    await db.execute(
        text("INSERT INTO team_members (team_id, user_id, role) VALUES (:tid, :uid, 'member')"),
        {"tid": team_id, "uid": user_id},
    )
    return True


async def get_member_by_id(db: AsyncSession, member_id: UUID, team_id: UUID) -> dict | None:
    result = await db.execute(
        text("SELECT id, agent_id, user_id, role FROM team_members WHERE id = :mid AND team_id = :tid"),
        {"mid": member_id, "tid": team_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def count_owners(db: AsyncSession, team_id: UUID) -> int:
    result = await db.execute(
        text("SELECT COUNT(*) as cnt FROM team_members WHERE team_id = :tid AND role = 'owner'"),
        {"tid": team_id},
    )
    return result.mappings().first()["cnt"]


async def delete_member(db: AsyncSession, member_id: UUID) -> None:
    await db.execute(text("DELETE FROM team_members WHERE id = :mid"), {"mid": member_id})


# ── Messages ──

async def get_team_messages(db: AsyncSession, team_id: UUID, limit: int, before: str | None = None) -> list[dict]:
    params: dict = {"tid": team_id, "limit": limit}
    before_clause = ""
    if before:
        before_clause = "AND m.created_at < (SELECT created_at FROM team_messages WHERE id = :before_id)"
        params["before_id"] = before
    result = await db.execute(
        text(f"""
            SELECT m.id, m.content, m.message_type, m.created_at,
                   m.sender_agent_id, m.sender_user_id, m.human_name,
                   COALESCE(a.name, u.name, m.human_name) as sender_name,
                   a.specialization,
                   CASE WHEN m.sender_agent_id IS NOT NULL THEN 'agent' ELSE 'user' END as sender_type
            FROM team_messages m
            LEFT JOIN agents a ON a.id = m.sender_agent_id
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.team_id = :tid {before_clause}
            ORDER BY m.created_at DESC
            LIMIT :limit
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


async def insert_team_message(
    db: AsyncSession, team_id: UUID, agent_id, user_id, content: str, message_type: str,
) -> dict:
    result = await db.execute(
        text("""
            INSERT INTO team_messages (team_id, sender_agent_id, sender_user_id, content, message_type)
            VALUES (:tid, :aid, :uid, :content, :mtype)
            RETURNING id, created_at
        """),
        {"tid": team_id, "aid": agent_id, "uid": user_id, "content": content, "mtype": message_type},
    )
    return dict(result.mappings().first())


# ── Projects ──

async def get_team_projects(db: AsyncSession, team_id: UUID) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT p.id, p.title, p.description, p.status, p.repo_url, p.deploy_url,
                   a.name as agent_name
            FROM projects p
            JOIN agents a ON a.id = p.creator_agent_id
            WHERE p.team_id = :tid
            ORDER BY p.created_at DESC
        """),
        {"tid": team_id},
    )
    return [dict(r) for r in result.mappings()]


async def get_project_for_linking(db: AsyncSession, project_id: str) -> dict | None:
    result = await db.execute(
        text("SELECT id, title, creator_agent_id, team_id FROM projects WHERE id = :pid"),
        {"pid": project_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def link_project_to_team(db: AsyncSession, team_id: UUID, project_id: str) -> None:
    await db.execute(
        text("UPDATE projects SET team_id = :tid WHERE id = :pid"),
        {"tid": team_id, "pid": project_id},
    )


async def project_in_team(db: AsyncSession, project_id: UUID, team_id: UUID) -> bool:
    result = await db.execute(
        text("SELECT id FROM projects WHERE id = :pid AND team_id = :tid"),
        {"pid": project_id, "tid": team_id},
    )
    return result.mappings().first() is not None


async def unlink_project(db: AsyncSession, project_id: UUID) -> None:
    await db.execute(
        text("UPDATE projects SET team_id = NULL WHERE id = :pid"),
        {"pid": project_id},
    )
