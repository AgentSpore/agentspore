"""
Project Governance API
======================
Управление contributor-ами проекта и внешними действиями (PR/push от не-платформенных акторов).

Endpoints:
  GET    /projects/:id/governance          — очередь pending действий
  POST   /projects/:id/governance/:item/vote — голосование (approve/reject)
  GET    /projects/:id/contributors        — список contributor-ов
  POST   /projects/:id/contributors        — добавить contributor-а (owner/admin)
  DELETE /projects/:id/contributors/:uid  — удалить contributor-а
  POST   /projects/:id/contributors/join  — запрос на вступление (любой пользователь)
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import CurrentUser, DatabaseSession, OptionalUser
from app.services.git_service import get_git_service

logger = logging.getLogger("governance")
router = APIRouter(prefix="/projects", tags=["governance"])


# ─── Schemas ─────────────────────────────────────────────────────────────────

class VoteRequest(BaseModel):
    vote: str           # "approve" | "reject"
    comment: str = ""   # опциональный комментарий к голосу


class AddContributorRequest(BaseModel):
    user_id: UUID
    role: str = "contributor"   # contributor | admin


class JoinRequest(BaseModel):
    message: str = ""           # мотивационное сообщение


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_project(db: DatabaseSession, project_id: UUID) -> dict:
    row = await db.execute(
        text("SELECT id, title, creator_agent_id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    project = row.mappings().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return dict(project)


async def _get_contributor(db: DatabaseSession, project_id: UUID, user_id: UUID) -> dict | None:
    row = await db.execute(
        text("SELECT id, role FROM project_members WHERE project_id = :pid AND user_id = :uid"),
        {"pid": project_id, "uid": user_id},
    )
    first = row.mappings().first()
    return dict(first) if first else None


async def _execute_governance_decision(
    db: DatabaseSession,
    item_id: UUID,
    project_id: UUID,
    action_type: str,
    source_number: int | None,
    project_title: str,
    approved: bool,
    voter_user_id: UUID,
) -> None:
    """Исполнить решение governance через GitHub App."""
    git = get_git_service()

    if action_type == "external_pr" and source_number:
        if approved:
            ok = await git.merge_pull_request(
                project_title,
                source_number,
                commit_message=f"Approved by project contributors via AgentSpore governance",
            )
            status_str = "executed" if ok else "approved"
            # Contributor-ы, одобрившие PR, получают contribution_points (batch INSERT...SELECT)
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
        else:
            await git.close_pull_request(project_title, source_number)
            status_str = "executed"

    elif action_type == "add_contributor":
        if approved:
            meta_row = await db.execute(
                text("SELECT meta FROM governance_queue WHERE id = :id"),
                {"id": item_id},
            )
            meta = (meta_row.mappings().first() or {}).get("meta", {})
            new_user_id = meta.get("user_id") if meta else None
            if new_user_id:
                await db.execute(
                    text("""
                        INSERT INTO project_members (project_id, user_id, invited_by_user_id)
                        VALUES (:pid, :uid, :inv)
                        ON CONFLICT (project_id, user_id) DO NOTHING
                    """),
                    {"pid": project_id, "uid": new_user_id, "inv": voter_user_id},
                )
        status_str = "executed"
    else:
        status_str = "executed"

    await db.execute(
        text("""
            UPDATE governance_queue
            SET status = :status, resolved_at = NOW()
            WHERE id = :id
        """),
        {"status": status_str, "id": item_id},
    )


# ─── Governance Queue ─────────────────────────────────────────────────────────

@router.get("/{project_id}/governance")
async def list_governance_queue(
    project_id: UUID,
    status: str = Query(default="pending", pattern="^(pending|approved|rejected|expired|executed|all)$"),
    db: DatabaseSession = ...,
    current_user: OptionalUser = None,
):
    """
    Список действий в очереди governance. Публичный просмотр, my_vote только для авторизованных.

    pending — ожидают голосования
    all     — вся история
    """
    await _get_project(db, project_id)

    where = "WHERE gq.project_id = :pid" + ("" if status == "all" else " AND gq.status = :status")
    params: dict[str, Any] = {"pid": project_id, "uid": current_user.id if current_user else None}
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
    items = [dict(r) for r in rows.mappings()]
    return {"items": items, "total": len(items), "status_filter": status}


@router.post("/{project_id}/governance/{item_id}/vote")
async def cast_vote(
    project_id: UUID,
    item_id: UUID,
    body: VoteRequest,
    db: DatabaseSession = ...,
    current_user: CurrentUser = ...,
):
    """
    Проголосовать за/против внешнего действия.

    Только contributor-ы и admin-ы проекта могут голосовать.
    Один голос на пользователя. При достижении порога действие исполняется автоматически.
    """
    if body.vote not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="vote must be 'approve' or 'reject'")

    # Проверить что пользователь — contributor
    contributor = await _get_contributor(db, project_id, current_user.id)
    if not contributor:
        raise HTTPException(status_code=403, detail="Only project contributors can vote")

    # Получить item
    item_row = await db.execute(
        text("""
            SELECT id, action_type, source_number, status, votes_required,
                   votes_approve, votes_reject
            FROM governance_queue
            WHERE id = :iid AND project_id = :pid
        """),
        {"iid": item_id, "pid": project_id},
    )
    item = item_row.mappings().first()
    if not item:
        raise HTTPException(status_code=404, detail="Governance item not found")
    if item["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Item is already '{item['status']}'")

    # Записать голос (UPSERT — можно передумать)
    await db.execute(
        text("""
            INSERT INTO governance_votes (queue_item_id, user_id, vote, comment)
            VALUES (:item_id, :uid, :vote, :comment)
            ON CONFLICT (queue_item_id, user_id)
            DO UPDATE SET vote = :vote, comment = :comment, created_at = NOW()
        """),
        {"item_id": item_id, "uid": current_user.id, "vote": body.vote, "comment": body.comment},
    )

    # Пересчитать счётчики
    counts_row = await db.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE vote = 'approve') AS approve_count,
                COUNT(*) FILTER (WHERE vote = 'reject')  AS reject_count
            FROM governance_votes WHERE queue_item_id = :item_id
        """),
        {"item_id": item_id},
    )
    counts = counts_row.mappings().first()
    votes_approve = counts["approve_count"]
    votes_reject = counts["reject_count"]

    await db.execute(
        text("UPDATE governance_queue SET votes_approve = :a, votes_reject = :r WHERE id = :id"),
        {"a": votes_approve, "r": votes_reject, "id": item_id},
    )

    required = item["votes_required"]
    decision_reached = votes_approve >= required or votes_reject >= required

    if decision_reached:
        approved = votes_approve >= required
        new_status = "approved" if approved else "rejected"

        await db.execute(
            text("UPDATE governance_queue SET status = :s WHERE id = :id"),
            {"s": new_status, "id": item_id},
        )

        # Получить данные проекта для исполнения
        project = await _get_project(db, project_id)
        await _execute_governance_decision(
            db=db,
            item_id=item_id,
            project_id=project_id,
            action_type=item["action_type"],
            source_number=item["source_number"],
            project_title=project["title"],
            approved=approved,
            voter_user_id=current_user.id,
        )
        await db.commit()
        return {
            "status": "decision_reached",
            "decision": "approved" if approved else "rejected",
            "votes_approve": votes_approve,
            "votes_reject": votes_reject,
        }

    await db.commit()
    return {
        "status": "vote_recorded",
        "vote": body.vote,
        "votes_approve": votes_approve,
        "votes_reject": votes_reject,
        "votes_required": required,
    }


# ─── Contributors ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/contributors")
async def list_contributors(
    project_id: UUID,
    db: DatabaseSession = ...,
    current_user: OptionalUser = None,
):
    """Список contributor-ов проекта с их вкладом. Публичный."""
    await _get_project(db, project_id)

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
    contributors = [dict(r) for r in rows.mappings()]
    return {"contributors": contributors, "total": len(contributors)}


@router.post("/{project_id}/contributors")
async def add_contributor(
    project_id: UUID,
    body: AddContributorRequest,
    db: DatabaseSession = ...,
    current_user: CurrentUser = ...,
):
    """
    Добавить contributor-а напрямую (только admin или владелец агента).

    Для обычных пользователей — используйте /contributors/join.
    """
    project = await _get_project(db, project_id)

    # Проверить что текущий пользователь — admin или owner агента
    caller = await _get_contributor(db, project_id, current_user.id)
    is_agent_owner_row = await db.execute(
        text("SELECT 1 FROM agents WHERE id = :aid AND owner_user_id = :uid"),
        {"aid": project["creator_agent_id"], "uid": current_user.id},
    )
    is_agent_owner = bool(is_agent_owner_row.first())

    if not is_agent_owner and (not caller or caller["role"] != "admin"):
        raise HTTPException(status_code=403, detail="Only project admin or agent owner can add contributors")

    # Проверить что user существует
    user_row = await db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": body.user_id})
    if not user_row.first():
        raise HTTPException(status_code=404, detail="User not found")

    await db.execute(
        text("""
            INSERT INTO project_members (project_id, user_id, role, invited_by_user_id)
            VALUES (:pid, :uid, :role, :inv)
            ON CONFLICT (project_id, user_id) DO UPDATE SET role = :role
        """),
        {"pid": project_id, "uid": body.user_id, "role": body.role, "inv": current_user.id},
    )
    await db.commit()
    return {"status": "added", "user_id": str(body.user_id), "role": body.role}


@router.post("/{project_id}/contributors/join")
async def request_to_join(
    project_id: UUID,
    body: JoinRequest,
    db: DatabaseSession = ...,
    current_user: CurrentUser = ...,
):
    """
    Запрос на вступление в проект как contributor.

    Создаёт элемент в governance_queue — существующие contributor-ы голосуют.
    Если contributor-ов нет — принимается автоматически.
    """
    await _get_project(db, project_id)

    # Уже contributor?
    existing = await _get_contributor(db, project_id, current_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="You are already a contributor")

    # Сколько contributor-ов уже есть?
    count_row = await db.execute(
        text("SELECT COUNT(*) as cnt FROM project_members WHERE project_id = :pid"),
        {"pid": project_id},
    )
    contributor_count = count_row.mappings().first()["cnt"]

    if contributor_count == 0:
        # Нет contributor-ов → автоматически принять первого
        await db.execute(
            text("""
                INSERT INTO project_members (project_id, user_id)
                VALUES (:pid, :uid)
                ON CONFLICT DO NOTHING
            """),
            {"pid": project_id, "uid": current_user.id},
        )
        await db.commit()
        return {"status": "auto_approved", "message": "You are now the first contributor of this project"}

    # Создать governance_queue item для голосования
    votes_required = min(2, contributor_count)   # 1 из N, но не больше 2
    await db.execute(
        text("""
            INSERT INTO governance_queue
                (project_id, action_type, source_ref, actor_login, meta, votes_required)
            VALUES
                (:pid, 'add_contributor',
                 :ref, :login,
                 CAST(:meta AS jsonb),
                 :votes_req)
            ON CONFLICT DO NOTHING
        """),
        {
            "pid": project_id,
            "ref": f"https://agentspore.com/projects/{project_id}/contributors",
            "login": getattr(current_user, "email", str(current_user.id)),
            "meta": f'{{"user_id": "{current_user.id}", "message": "{body.message[:200]}"}}',
            "votes_req": votes_required,
        },
    )
    await db.commit()
    return {
        "status": "pending_approval",
        "message": f"Your request is pending approval from {votes_required} contributor(s)",
    }


@router.delete("/{project_id}/contributors/{user_id}")
async def remove_contributor(
    project_id: UUID,
    user_id: UUID,
    db: DatabaseSession = ...,
    current_user: CurrentUser = ...,
):
    """Удалить contributor-а (только admin или сам пользователь)."""
    project = await _get_project(db, project_id)
    caller = await _get_contributor(db, project_id, current_user.id)

    is_agent_owner_row = await db.execute(
        text("SELECT 1 FROM agents WHERE id = :aid AND owner_user_id = :uid"),
        {"aid": project["creator_agent_id"], "uid": current_user.id},
    )
    is_self = str(current_user.id) == str(user_id)
    is_admin = caller and caller["role"] == "admin"
    is_owner = bool(is_agent_owner_row.first())

    if not (is_self or is_admin or is_owner):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    await db.execute(
        text("DELETE FROM project_members WHERE project_id = :pid AND user_id = :uid"),
        {"pid": project_id, "uid": user_id},
    )
    await db.commit()
    return {"status": "removed", "user_id": str(user_id)}
