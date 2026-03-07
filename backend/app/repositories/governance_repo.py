"""Governance repository — governance_queue, governance_votes, project_members queries."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def get_project(db: AsyncSession, project_id: UUID) -> dict | None:
    row = await db.execute(
        text("SELECT id, title, creator_agent_id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    first = row.mappings().first()
    return dict(first) if first else None


async def get_contributor(db: AsyncSession, project_id: UUID, user_id: UUID) -> dict | None:
    row = await db.execute(
        text("SELECT id, role FROM project_members WHERE project_id = :pid AND user_id = :uid"),
        {"pid": project_id, "uid": user_id},
    )
    first = row.mappings().first()
    return dict(first) if first else None


async def list_governance_queue(
    db: AsyncSession, project_id: UUID, status: str, user_id: UUID | None,
) -> list[dict]:
    where = "WHERE gq.project_id = :pid" + ("" if status == "all" else " AND gq.status = :status")
    params: dict[str, Any] = {"pid": project_id, "uid": user_id}
    if status != "all":
        params["status"] = status

    rows = await db.execute(
        text(f"""
            SELECT
                gq.id, gq.action_type, gq.source_ref, gq.source_number,
                gq.actor_login, gq.actor_type, gq.meta,
                gq.status, gq.votes_required, gq.votes_approve, gq.votes_reject,
                gq.expires_at, gq.created_at, gq.resolved_at,
                gv.vote as my_vote
            FROM governance_queue gq
            LEFT JOIN governance_votes gv
                ON gv.queue_item_id = gq.id AND gv.user_id = :uid
            {where}
            ORDER BY gq.created_at DESC
            LIMIT 100
        """),
        params,
    )
    return [dict(r) for r in rows.mappings()]


async def get_governance_item(db: AsyncSession, item_id: UUID, project_id: UUID) -> dict | None:
    row = await db.execute(
        text("""
            SELECT id, action_type, source_number, status, votes_required,
                   votes_approve, votes_reject
            FROM governance_queue
            WHERE id = :iid AND project_id = :pid
        """),
        {"iid": item_id, "pid": project_id},
    )
    first = row.mappings().first()
    return dict(first) if first else None


async def upsert_vote(db: AsyncSession, item_id: UUID, user_id: UUID, vote: str, comment: str | None) -> None:
    await db.execute(
        text("""
            INSERT INTO governance_votes (queue_item_id, user_id, vote, comment)
            VALUES (:item_id, :uid, :vote, :comment)
            ON CONFLICT (queue_item_id, user_id)
            DO UPDATE SET vote = :vote, comment = :comment, created_at = NOW()
        """),
        {"item_id": item_id, "uid": user_id, "vote": vote, "comment": comment},
    )


async def count_votes(db: AsyncSession, item_id: UUID) -> dict:
    row = await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE vote = 'approve') AS approve_count,
                COUNT(*) FILTER (WHERE vote = 'reject')  AS reject_count
            FROM governance_votes WHERE queue_item_id = :item_id
        """),
        {"item_id": item_id},
    )
    return dict(row.mappings().first())


async def update_vote_counts(db: AsyncSession, item_id: UUID, approve: int, reject: int) -> None:
    await db.execute(
        text("UPDATE governance_queue SET votes_approve = :a, votes_reject = :r WHERE id = :id"),
        {"a": approve, "r": reject, "id": item_id},
    )


async def update_governance_status(db: AsyncSession, item_id: UUID, status: str) -> None:
    await db.execute(
        text("UPDATE governance_queue SET status = :s WHERE id = :id"),
        {"s": status, "id": item_id},
    )


async def award_contribution_points(db: AsyncSession, project_id: UUID, item_id: UUID) -> None:
    await db.execute(
        text("""
            INSERT INTO project_members (project_id, user_id, contribution_points)
            SELECT :pid, gv.user_id, 10
            FROM governance_votes gv
            WHERE gv.queue_item_id = :item_id AND gv.vote = 'approve'
            ON CONFLICT (project_id, user_id)
            DO UPDATE SET contribution_points = project_members.contribution_points + 10
        """),
        {"pid": project_id, "item_id": item_id},
    )


async def get_governance_meta(db: AsyncSession, item_id: UUID) -> dict | None:
    row = await db.execute(
        text("SELECT meta FROM governance_queue WHERE id = :id"),
        {"id": item_id},
    )
    first = row.mappings().first()
    return first["meta"] if first else None


async def insert_contributor(db: AsyncSession, project_id: UUID, user_id, invited_by: UUID) -> None:
    await db.execute(
        text("""
            INSERT INTO project_members (project_id, user_id, invited_by_user_id)
            VALUES (:pid, :uid, :inv)
            ON CONFLICT (project_id, user_id) DO NOTHING
        """),
        {"pid": project_id, "uid": user_id, "inv": invited_by},
    )


async def resolve_governance_item(db: AsyncSession, item_id: UUID, status: str) -> None:
    await db.execute(
        text("UPDATE governance_queue SET status = :status, resolved_at = NOW() WHERE id = :id"),
        {"status": status, "id": item_id},
    )


async def list_contributors(db: AsyncSession, project_id: UUID) -> list[dict]:
    rows = await db.execute(
        text("""
            SELECT
                pc.id, pc.role, pc.contribution_points, pc.joined_at,
                u.id as user_id, u.name as user_name, u.email as user_email,
                u.wallet_address
            FROM project_members pc
            JOIN users u ON u.id = pc.user_id
            WHERE pc.project_id = :pid
            ORDER BY pc.contribution_points DESC, pc.joined_at
        """),
        {"pid": project_id},
    )
    return [dict(r) for r in rows.mappings()]


async def is_agent_owner(db: AsyncSession, agent_id, user_id: UUID) -> bool:
    row = await db.execute(
        text("SELECT 1 FROM agents WHERE id = :aid AND owner_user_id = :uid"),
        {"aid": agent_id, "uid": user_id},
    )
    return bool(row.first())


async def user_exists(db: AsyncSession, user_id) -> bool:
    row = await db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
    return bool(row.first())


async def upsert_contributor(db: AsyncSession, project_id: UUID, user_id, role: str, invited_by: UUID) -> None:
    await db.execute(
        text("""
            INSERT INTO project_members (project_id, user_id, role, invited_by_user_id)
            VALUES (:pid, :uid, :role, :inv)
            ON CONFLICT (project_id, user_id) DO UPDATE SET role = :role
        """),
        {"pid": project_id, "uid": user_id, "role": role, "inv": invited_by},
    )


async def count_contributors(db: AsyncSession, project_id: UUID) -> int:
    row = await db.execute(
        text("SELECT COUNT(*) as cnt FROM project_members WHERE project_id = :pid"),
        {"pid": project_id},
    )
    return row.mappings().first()["cnt"]


async def auto_approve_contributor(db: AsyncSession, project_id: UUID, user_id: UUID) -> None:
    await db.execute(
        text("INSERT INTO project_members (project_id, user_id) VALUES (:pid, :uid) ON CONFLICT DO NOTHING"),
        {"pid": project_id, "uid": user_id},
    )


async def create_join_request(
    db: AsyncSession, project_id: UUID, source_ref: str, login: str, meta_json: str, votes_required: int,
) -> None:
    await db.execute(
        text("""
            INSERT INTO governance_queue
                (project_id, action_type, source_ref, actor_login, meta, votes_required)
            VALUES
                (:pid, 'add_contributor', :ref, :login, CAST(:meta AS jsonb), :votes_req)
            ON CONFLICT DO NOTHING
        """),
        {"pid": project_id, "ref": source_ref, "login": login, "meta": meta_json, "votes_req": votes_required},
    )


async def delete_contributor(db: AsyncSession, project_id: UUID, user_id: UUID) -> None:
    await db.execute(
        text("DELETE FROM project_members WHERE project_id = :pid AND user_id = :uid"),
        {"pid": project_id, "uid": user_id},
    )
