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
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DatabaseSession, OptionalUser
from app.repositories import governance_repo
from app.schemas.governance import AddContributorRequest, JoinRequest, VoteRequest
from app.services.git_service import get_git_service

logger = logging.getLogger("governance")
router = APIRouter(prefix="/projects", tags=["governance"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_project_or_404(db: DatabaseSession, project_id: UUID) -> dict:
    project = await governance_repo.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


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
            await governance_repo.award_contribution_points(db, project_id, item_id)
        else:
            await git.close_pull_request(project_title, source_number)
            status_str = "executed"

    elif action_type == "add_contributor":
        if approved:
            meta = await governance_repo.get_governance_meta(db, item_id)
            new_user_id = meta.get("user_id") if meta else None
            if new_user_id:
                await governance_repo.insert_contributor(db, project_id, new_user_id, voter_user_id)
        status_str = "executed"
    else:
        status_str = "executed"

    await governance_repo.resolve_governance_item(db, item_id, status_str)


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
    await _get_project_or_404(db, project_id)

    user_id = current_user.id if current_user else None
    items = await governance_repo.list_governance_queue(db, project_id, status, user_id)
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

    contributor = await governance_repo.get_contributor(db, project_id, current_user.id)
    if not contributor:
        raise HTTPException(status_code=403, detail="Only project contributors can vote")

    item = await governance_repo.get_governance_item(db, item_id, project_id)
    if not item:
        raise HTTPException(status_code=404, detail="Governance item not found")
    if item["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Item is already '{item['status']}'")

    await governance_repo.upsert_vote(db, item_id, current_user.id, body.vote, body.comment)

    counts = await governance_repo.count_votes(db, item_id)
    votes_approve = counts["approve_count"]
    votes_reject = counts["reject_count"]

    await governance_repo.update_vote_counts(db, item_id, votes_approve, votes_reject)

    required = item["votes_required"]
    decision_reached = votes_approve >= required or votes_reject >= required

    if decision_reached:
        approved = votes_approve >= required
        new_status = "approved" if approved else "rejected"
        await governance_repo.update_governance_status(db, item_id, new_status)

        project = await _get_project_or_404(db, project_id)
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
    await _get_project_or_404(db, project_id)

    contributors = await governance_repo.list_contributors(db, project_id)
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
    project = await _get_project_or_404(db, project_id)

    caller = await governance_repo.get_contributor(db, project_id, current_user.id)
    is_owner = await governance_repo.is_agent_owner(db, project["creator_agent_id"], current_user.id)

    if not is_owner and (not caller or caller["role"] != "admin"):
        raise HTTPException(status_code=403, detail="Only project admin or agent owner can add contributors")

    if not await governance_repo.user_exists(db, body.user_id):
        raise HTTPException(status_code=404, detail="User not found")

    await governance_repo.upsert_contributor(db, project_id, body.user_id, body.role, current_user.id)
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
    await _get_project_or_404(db, project_id)

    existing = await governance_repo.get_contributor(db, project_id, current_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="You are already a contributor")

    contributor_count = await governance_repo.count_contributors(db, project_id)

    if contributor_count == 0:
        await governance_repo.auto_approve_contributor(db, project_id, current_user.id)
        await db.commit()
        return {"status": "auto_approved", "message": "You are now the first contributor of this project"}

    votes_required = min(2, contributor_count)
    await governance_repo.create_join_request(
        db,
        project_id=project_id,
        source_ref=f"https://agentspore.com/projects/{project_id}/contributors",
        login=getattr(current_user, "email", str(current_user.id)),
        meta_json=f'{{"user_id": "{current_user.id}", "message": "{body.message[:200]}"}}',
        votes_required=votes_required,
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
    project = await _get_project_or_404(db, project_id)
    caller = await governance_repo.get_contributor(db, project_id, current_user.id)

    is_self = str(current_user.id) == str(user_id)
    is_admin = caller and caller["role"] == "admin"
    is_owner = await governance_repo.is_agent_owner(db, project["creator_agent_id"], current_user.id)

    if not (is_self or is_admin or is_owner):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    await governance_repo.delete_contributor(db, project_id, user_id)
    await db.commit()
    return {"status": "removed", "user_id": str(user_id)}
