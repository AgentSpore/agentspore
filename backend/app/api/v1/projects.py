"""
Projects API — публичный просмотр проектов и голосование
=========================================================
GET  /projects         — список проектов (с фильтрами)
POST /projects/{id}/vote — проголосовать за/против
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import project_repo
from app.schemas.projects import VoteRequest

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
async def list_projects(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    hackathon_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Публичный список проектов — для UI."""
    return await project_repo.list_projects(
        db, limit=limit, offset=offset, category=category, status=status, hackathon_id=hackathon_id,
    )


@router.get("/{project_id}")
async def get_project(project_id: UUID, db: AsyncSession = Depends(get_db)):
    """Публичные данные одного проекта."""
    project = await project_repo.get_project_by_id(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/{project_id}/vote")
async def vote_project(
    project_id: UUID,
    body: VoteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Голосование за проект. Дедупликация по IP — один голос на проект.
    vote: 1 = upvote, -1 = downvote. Повторный голос с того же IP обновляет значение.

    Rate limit: макс. 10 голосов/час с одного IP, cooldown 5 сек между голосами.
    """
    if not await project_repo.project_exists(db, project_id):
        raise HTTPException(status_code=404, detail="Project not found")

    voter_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not voter_ip:
        voter_ip = request.client.host if request.client else "unknown"

    # Rate limit
    if await project_repo.count_votes_in_period(db, voter_ip) >= 10:
        raise HTTPException(status_code=429, detail="Too many votes. Max 10 votes per hour.")

    # Cooldown
    last_at = await project_repo.get_last_vote_time(db, voter_ip)
    if last_at:
        now = datetime.now(timezone.utc)
        diff = (now - last_at.replace(tzinfo=timezone.utc)).total_seconds()
        if diff < 5:
            raise HTTPException(status_code=429, detail="Please wait a few seconds between votes.")

    prev_vote = await project_repo.get_previous_vote(db, project_id, voter_ip)

    if prev_vote:
        old_value = prev_vote["value"]
        if old_value == body.vote:
            counts = await project_repo.get_vote_counts(db, project_id)
            return {
                "project_id": str(project_id),
                "votes_up": counts["votes_up"],
                "votes_down": counts["votes_down"],
                "score": counts["votes_up"] - counts["votes_down"],
            }

        await project_repo.update_vote_value(db, prev_vote["id"], body.vote)
        await project_repo.swap_vote_counts(db, project_id, old_value)
    else:
        await project_repo.insert_vote(db, project_id, voter_ip, body.vote)

    await db.commit()

    counts = await project_repo.get_vote_counts(db, project_id)
    return {
        "project_id": str(project_id),
        "votes_up": counts["votes_up"],
        "votes_down": counts["votes_down"],
        "score": counts["votes_up"] - counts["votes_down"],
    }
