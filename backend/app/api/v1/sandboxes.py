"""API для песочниц."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AISvc, CurrentUser, DatabaseSession, TokenSvc
from app.models import Feedback, Feature, Idea, Sandbox, TokenAction

router = APIRouter(prefix="/sandboxes", tags=["sandboxes"])


# === Schemas ===


class SandboxResponse(BaseModel):
    """Песочница в ответе."""

    id: uuid.UUID
    idea_id: uuid.UUID
    idea_title: str
    prototype_url: str
    feedbacks_count: int
    features_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class SandboxDetailResponse(SandboxResponse):
    """Детальная информация о песочнице."""

    prototype_html: str


class FeedbackCreate(BaseModel):
    """Создание фидбэка."""

    rating: int  # 1-5
    comment: str


class FeedbackResponse(BaseModel):
    """Фидбэк в ответе."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None
    rating: int
    comment: str
    created_at: datetime


class FeatureCreate(BaseModel):
    """Создание фичи."""

    title: str
    description: str


class FeatureResponse(BaseModel):
    """Фича в ответе."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None
    title: str
    description: str
    votes: int
    created_at: datetime


# === Endpoints ===


@router.get("", response_model=list[SandboxResponse])
async def list_sandboxes(db: DatabaseSession):
    """Получить список песочниц."""
    result = await db.execute(
        select(Sandbox).order_by(Sandbox.created_at.desc())
    )
    sandboxes = result.scalars().all()

    items = []
    for sandbox in sandboxes:
        # Получаем название идеи
        idea_result = await db.execute(
            select(Idea.title).where(Idea.id == sandbox.idea_id)
        )
        idea_title = idea_result.scalar_one_or_none() or "Unknown"

        items.append(
            SandboxResponse(
                id=sandbox.id,
                idea_id=sandbox.idea_id,
                idea_title=idea_title,
                prototype_url=sandbox.prototype_url,
                feedbacks_count=sandbox.feedbacks_count,
                features_count=sandbox.features_count,
                created_at=sandbox.created_at,
            )
        )

    return items


@router.post("/{idea_id}", response_model=SandboxDetailResponse, status_code=201)
async def create_sandbox(
    idea_id: uuid.UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    ai_service: AISvc,
):
    """Создать песочницу для идеи."""
    # Проверяем существование идеи
    result = await db.execute(select(Idea).where(Idea.id == idea_id))
    idea = result.scalar_one_or_none()

    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Проверяем что песочница ещё не создана
    sandbox_result = await db.execute(
        select(Sandbox).where(Sandbox.idea_id == idea_id)
    )
    if sandbox_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Sandbox already exists")

    # Генерируем HTML прототип
    prototype_html = await ai_service.generate_prototype_html(idea.title, idea.solution)

    # Создаём песочницу
    sandbox = Sandbox(
        idea_id=idea_id,
        prototype_url=f"/sandbox/{idea_id}",
        prototype_html=prototype_html,
    )
    db.add(sandbox)

    # Обновляем статус идеи
    idea.status = "sandbox"

    await db.flush()

    return SandboxDetailResponse(
        id=sandbox.id,
        idea_id=sandbox.idea_id,
        idea_title=idea.title,
        prototype_url=sandbox.prototype_url,
        prototype_html=sandbox.prototype_html,
        feedbacks_count=0,
        features_count=0,
        created_at=sandbox.created_at,
    )


@router.get("/{sandbox_id}", response_model=SandboxDetailResponse)
async def get_sandbox(sandbox_id: uuid.UUID, db: DatabaseSession):
    """Получить песочницу по ID."""
    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    # Получаем название идеи
    idea_result = await db.execute(
        select(Idea.title).where(Idea.id == sandbox.idea_id)
    )
    idea_title = idea_result.scalar_one_or_none() or "Unknown"

    return SandboxDetailResponse(
        id=sandbox.id,
        idea_id=sandbox.idea_id,
        idea_title=idea_title,
        prototype_url=sandbox.prototype_url,
        prototype_html=sandbox.prototype_html,
        feedbacks_count=sandbox.feedbacks_count,
        features_count=sandbox.features_count,
        created_at=sandbox.created_at,
    )


@router.post("/{sandbox_id}/feedback", response_model=FeedbackResponse, status_code=201)
async def add_feedback(
    sandbox_id: uuid.UUID,
    data: FeedbackCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
    token_service: TokenSvc,
):
    """Добавить фидбэк к песочнице."""
    # Проверяем песочницу
    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    # Валидация рейтинга
    if data.rating < 1 or data.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be 1-5")

    # Создаём фидбэк
    feedback = Feedback(
        sandbox_id=sandbox_id,
        user_id=current_user.id,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(feedback)

    # Увеличиваем счётчик
    sandbox.feedbacks_count += 1

    # Начисляем токены
    await token_service.award_tokens(
        user_id=current_user.id,
        action=TokenAction.FEEDBACK,
        idea_id=sandbox.idea_id,
    )

    await db.flush()

    return FeedbackResponse(
        id=feedback.id,
        user_id=feedback.user_id,
        user_name=current_user.name,
        rating=feedback.rating,
        comment=feedback.comment,
        created_at=feedback.created_at,
    )


@router.get("/{sandbox_id}/features", response_model=list[FeatureResponse])
async def get_features(sandbox_id: uuid.UUID, db: DatabaseSession):
    """Получить список фичей песочницы."""
    result = await db.execute(
        select(Feature)
        .where(Feature.sandbox_id == sandbox_id)
        .order_by(Feature.votes.desc())
    )
    features = result.scalars().all()

    from app.models import User

    items = []
    for feature in features:
        user_result = await db.execute(
            select(User.name).where(User.id == feature.user_id)
        )
        user_name = user_result.scalar_one_or_none()

        items.append(
            FeatureResponse(
                id=feature.id,
                user_id=feature.user_id,
                user_name=user_name,
                title=feature.title,
                description=feature.description,
                votes=feature.votes,
                created_at=feature.created_at,
            )
        )

    return items


@router.post("/{sandbox_id}/features", response_model=FeatureResponse, status_code=201)
async def add_feature(
    sandbox_id: uuid.UUID,
    data: FeatureCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
    token_service: TokenSvc,
):
    """Предложить новую фичу."""
    # Проверяем песочницу
    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    # Создаём фичу
    feature = Feature(
        sandbox_id=sandbox_id,
        user_id=current_user.id,
        title=data.title,
        description=data.description,
    )
    db.add(feature)

    # Увеличиваем счётчик
    sandbox.features_count += 1

    # Начисляем токены
    await token_service.award_tokens(
        user_id=current_user.id,
        action=TokenAction.FEATURE,
        idea_id=sandbox.idea_id,
    )

    await db.flush()

    return FeatureResponse(
        id=feature.id,
        user_id=feature.user_id,
        user_name=current_user.name,
        title=feature.title,
        description=feature.description,
        votes=0,
        created_at=feature.created_at,
    )


# === Схемы для генерации кода ===


class CodeUpdateRequest(BaseModel):
    """Запрос на обновление кода."""

    code: str


class CodeModifyRequest(BaseModel):
    """Запрос на AI-модификацию кода."""

    prompt: str


class CodeGenerateResponse(BaseModel):
    """Ответ с сгенерированным кодом."""

    html: str
    features_applied: list[str]


class SandboxPreviewResponse(BaseModel):
    """Превью sandbox для iframe."""

    html: str


# === Endpoints для работы с кодом ===


@router.post("/{sandbox_id}/regenerate", response_model=CodeGenerateResponse)
async def regenerate_with_features(
    sandbox_id: uuid.UUID,
    db: DatabaseSession,
    current_user: CurrentUser,
    ai_service: AISvc,
):
    """Регенерировать код с учётом предложенных фич."""
    # Получаем песочницу
    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    # Получаем идею
    idea_result = await db.execute(select(Idea).where(Idea.id == sandbox.idea_id))
    idea = idea_result.scalar_one_or_none()

    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Получаем топ фичи по голосам
    features_result = await db.execute(
        select(Feature)
        .where(Feature.sandbox_id == sandbox_id)
        .order_by(Feature.votes.desc())
        .limit(10)
    )
    features = features_result.scalars().all()

    features_data = [
        {"title": f.title, "description": f.description, "votes": f.votes}
        for f in features
    ]

    # Генерируем новый код
    new_html = await ai_service.regenerate_prototype_with_features(
        idea_title=idea.title,
        idea_solution=idea.solution,
        current_html=sandbox.prototype_html,
        features=features_data,
    )

    # Обновляем sandbox
    sandbox.prototype_html = new_html
    await db.flush()

    return CodeGenerateResponse(
        html=new_html,
        features_applied=[f.title for f in features],
    )


@router.put("/{sandbox_id}/code", response_model=SandboxPreviewResponse)
async def update_code(
    sandbox_id: uuid.UUID,
    data: CodeUpdateRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
):
    """Обновить код песочницы напрямую."""
    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    sandbox.prototype_html = data.code
    await db.flush()

    return SandboxPreviewResponse(html=sandbox.prototype_html)


@router.post("/{sandbox_id}/modify", response_model=SandboxPreviewResponse)
async def modify_code_with_ai(
    sandbox_id: uuid.UUID,
    data: CodeModifyRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    ai_service: AISvc,
):
    """Модифицировать код через AI по текстовому запросу."""
    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    # Модифицируем код через AI
    new_html = await ai_service.modify_code_with_prompt(
        current_code=sandbox.prototype_html,
        modification_prompt=data.prompt,
        language="html",
    )

    # Обновляем sandbox
    sandbox.prototype_html = new_html
    await db.flush()

    return SandboxPreviewResponse(html=new_html)


@router.get("/{sandbox_id}/preview")
async def get_sandbox_preview(sandbox_id: uuid.UUID, db: DatabaseSession):
    """Получить HTML для отображения в iframe (публичный endpoint)."""
    from fastapi.responses import HTMLResponse

    result = await db.execute(select(Sandbox).where(Sandbox.id == sandbox_id))
    sandbox = result.scalar_one_or_none()

    if not sandbox:
        raise HTTPException(status_code=404, detail="Sandbox not found")

    return HTMLResponse(content=sandbox.prototype_html, media_type="text/html")
