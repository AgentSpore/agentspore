"""API для работы с идеями."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AISvc, CurrentUser, DatabaseSession, TokenSvc
from app.models import Comment, Idea, TokenAction, Vote

router = APIRouter(prefix="/ideas", tags=["ideas"])


# === Schemas ===


class IdeaCreate(BaseModel):
    """Создание идеи."""

    title: str
    problem: str
    solution: str
    category: str


class IdeaUpdate(BaseModel):
    """Обновление идеи."""

    title: str | None = None
    problem: str | None = None
    solution: str | None = None


class IdeaResponse(BaseModel):
    """Идея в ответе."""

    id: uuid.UUID
    title: str
    problem: str
    solution: str
    category: str
    author_id: uuid.UUID
    author_name: str | None = None
    votes_up: int
    votes_down: int
    score: int
    status: str
    ai_generated: bool
    comments_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class VoteRequest(BaseModel):
    """Голосование."""

    value: Literal[1, -1]


class CommentCreate(BaseModel):
    """Создание комментария."""

    content: str


class CommentResponse(BaseModel):
    """Комментарий в ответе."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class IdeasListResponse(BaseModel):
    """Список идей с пагинацией."""

    items: list[IdeaResponse]
    total: int
    page: int
    per_page: int


# === Endpoints ===


@router.get("", response_model=IdeasListResponse)
async def list_ideas(
    db: DatabaseSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
    status: str | None = None,
    sort: Literal["new", "popular", "trending"] = "new",
):
    """Получить список идей."""
    query = select(Idea)

    if category:
        query = query.where(Idea.category == category)
    if status:
        query = query.where(Idea.status == status)

    # Сортировка
    if sort == "new":
        query = query.order_by(Idea.created_at.desc())
    elif sort == "popular":
        query = query.order_by((Idea.votes_up - Idea.votes_down).desc())
    elif sort == "trending":
        # Trending: популярные за последние 7 дней
        from datetime import timedelta

        week_ago = datetime.utcnow() - timedelta(days=7)
        query = query.where(Idea.created_at >= week_ago).order_by(
            (Idea.votes_up - Idea.votes_down).desc()
        )

    # Общее количество
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Пагинация
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    result = await db.execute(query)
    ideas = result.scalars().all()

    # Формируем ответ с дополнительными данными
    items = []
    for idea in ideas:
        # Получаем имя автора
        from app.models import User

        author_result = await db.execute(select(User.name).where(User.id == idea.author_id))
        author_name = author_result.scalar_one_or_none()

        # Количество комментариев
        comments_count_result = await db.execute(
            select(func.count()).where(Comment.idea_id == idea.id)
        )
        comments_count = comments_count_result.scalar() or 0

        items.append(
            IdeaResponse(
                id=idea.id,
                title=idea.title,
                problem=idea.problem,
                solution=idea.solution,
                category=idea.category,
                author_id=idea.author_id,
                author_name=author_name,
                votes_up=idea.votes_up,
                votes_down=idea.votes_down,
                score=idea.votes_up - idea.votes_down,
                status=idea.status,
                ai_generated=idea.ai_generated,
                comments_count=comments_count,
                created_at=idea.created_at,
            )
        )

    return IdeasListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )


@router.post("", response_model=IdeaResponse, status_code=status.HTTP_201_CREATED)
async def create_idea(
    data: IdeaCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
    token_service: TokenSvc,
    ai_service: AISvc,
):
    """Создать новую идею."""
    # AI улучшает описание (опционально)
    problem = data.problem
    solution = data.solution
    try:
        improved = await ai_service.improve_idea_description(data.problem, data.solution)
        problem = improved.get("problem", data.problem)
        solution = improved.get("solution", data.solution)
    except Exception:
        # Если AI недоступен, используем оригинальный текст
        pass

    idea = Idea(
        title=data.title,
        problem=problem,
        solution=solution,
        category=data.category,
        author_id=current_user.id,
        ai_generated=False,
    )
    db.add(idea)
    await db.flush()

    # Начисляем токены за создание идеи
    await token_service.award_tokens(
        user_id=current_user.id,
        action=TokenAction.IDEA_CREATED,
        idea_id=idea.id,
    )

    return IdeaResponse(
        id=idea.id,
        title=idea.title,
        problem=idea.problem,
        solution=idea.solution,
        category=idea.category,
        author_id=idea.author_id,
        author_name=current_user.name,
        votes_up=0,
        votes_down=0,
        score=0,
        status=idea.status,
        ai_generated=idea.ai_generated,
        comments_count=0,
        created_at=idea.created_at,
    )


@router.get("/{idea_id}", response_model=IdeaResponse)
async def get_idea(idea_id: uuid.UUID, db: DatabaseSession):
    """Получить идею по ID."""
    result = await db.execute(select(Idea).where(Idea.id == idea_id))
    idea = result.scalar_one_or_none()

    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Получаем дополнительные данные
    from app.models import User

    author_result = await db.execute(select(User.name).where(User.id == idea.author_id))
    author_name = author_result.scalar_one_or_none()

    comments_count_result = await db.execute(
        select(func.count()).where(Comment.idea_id == idea.id)
    )
    comments_count = comments_count_result.scalar() or 0

    return IdeaResponse(
        id=idea.id,
        title=idea.title,
        problem=idea.problem,
        solution=idea.solution,
        category=idea.category,
        author_id=idea.author_id,
        author_name=author_name,
        votes_up=idea.votes_up,
        votes_down=idea.votes_down,
        score=idea.votes_up - idea.votes_down,
        status=idea.status,
        ai_generated=idea.ai_generated,
        comments_count=comments_count,
        created_at=idea.created_at,
    )


@router.post("/{idea_id}/vote")
async def vote_idea(
    idea_id: uuid.UUID,
    data: VoteRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    token_service: TokenSvc,
):
    """Проголосовать за идею."""
    # Проверяем существование идеи
    result = await db.execute(select(Idea).where(Idea.id == idea_id))
    idea = result.scalar_one_or_none()

    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Проверяем предыдущий голос
    vote_result = await db.execute(
        select(Vote).where(Vote.user_id == current_user.id, Vote.idea_id == idea_id)
    )
    existing_vote = vote_result.scalar_one_or_none()

    if existing_vote:
        # Обновляем голос
        old_value = existing_vote.value
        if old_value == data.value:
            raise HTTPException(status_code=400, detail="Already voted with this value")

        # Корректируем счётчики
        if old_value == 1:
            idea.votes_up -= 1
        else:
            idea.votes_down -= 1

        existing_vote.value = data.value
    else:
        # Новый голос
        vote = Vote(
            user_id=current_user.id,
            idea_id=idea_id,
            value=data.value,
        )
        db.add(vote)

        # Начисляем токены за голосование
        await token_service.award_tokens(
            user_id=current_user.id,
            action=TokenAction.VOTE,
            idea_id=idea_id,
        )

    # Обновляем счётчики
    if data.value == 1:
        idea.votes_up += 1
    else:
        idea.votes_down += 1

    await db.flush()

    return {"message": "Vote recorded", "score": idea.votes_up - idea.votes_down}


@router.get("/{idea_id}/comments", response_model=list[CommentResponse])
async def get_comments(idea_id: uuid.UUID, db: DatabaseSession):
    """Получить комментарии к идее."""
    result = await db.execute(
        select(Comment).where(Comment.idea_id == idea_id).order_by(Comment.created_at.desc())
    )
    comments = result.scalars().all()

    # Получаем имена пользователей
    from app.models import User

    items = []
    for comment in comments:
        user_result = await db.execute(select(User.name).where(User.id == comment.user_id))
        user_name = user_result.scalar_one_or_none()
        items.append(
            CommentResponse(
                id=comment.id,
                user_id=comment.user_id,
                user_name=user_name,
                content=comment.content,
                created_at=comment.created_at,
            )
        )

    return items


@router.post("/{idea_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    idea_id: uuid.UUID,
    data: CommentCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
    token_service: TokenSvc,
):
    """Добавить комментарий к идее."""
    # Проверяем существование идеи
    result = await db.execute(select(Idea).where(Idea.id == idea_id))
    idea = result.scalar_one_or_none()

    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    comment = Comment(
        user_id=current_user.id,
        idea_id=idea_id,
        content=data.content,
    )
    db.add(comment)
    await db.flush()

    # Начисляем токены за комментарий
    await token_service.award_tokens(
        user_id=current_user.id,
        action=TokenAction.COMMENT,
        idea_id=idea_id,
    )

    return CommentResponse(
        id=comment.id,
        user_id=comment.user_id,
        user_name=current_user.name,
        content=comment.content,
        created_at=comment.created_at,
    )
