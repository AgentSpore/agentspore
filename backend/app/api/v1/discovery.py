"""API для AI Discovery."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import AISvc, CurrentUser, DatabaseSession, TokenSvc
from app.models import Idea, TokenAction
from app.schemas.discovery import GenerateIdeaRequest, GeneratedIdeaResponse, ProblemCreate, ProblemResponse, ProblemUpdate

router = APIRouter(prefix="/discovery", tags=["discovery"])


# === Endpoints ===


@router.get("/problems", response_model=list[ProblemResponse])
async def discover_problems(
    ai_service: AISvc,
    category: str | None = None,
):
    """Найти проблемы для генерации идей (AI)."""
    try:
        problems = await ai_service.discover_problems(category)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"AI service unavailable. Configure LLM_API_KEY. Error: {str(e)}"
        )

    return [
        ProblemResponse(
            problem=p.get("problem", ""),
            source=p.get("source", ""),
            audience=p.get("audience", ""),
            importance=p.get("importance", ""),
        )
        for p in problems
    ]


@router.post("/generate", response_model=GeneratedIdeaResponse)
async def generate_idea_from_problem(
    data: GenerateIdeaRequest,
    ai_service: AISvc,
):
    """Сгенерировать идею стартапа из проблемы (AI)."""
    try:
        idea = await ai_service.generate_idea_from_problem(data.problem)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"AI service unavailable. Configure LLM_API_KEY. Error: {str(e)}"
        )

    return GeneratedIdeaResponse(
        title=idea.get("title", "Untitled"),
        problem=idea.get("problem", data.problem),
        solution=idea.get("solution", ""),
        features=idea.get("features", []),
        business_model=idea.get("business_model", ""),
        category=idea.get("category", "Other"),
    )


@router.post("/generate-and-save", status_code=status.HTTP_201_CREATED)
async def generate_and_save_idea(
    data: GenerateIdeaRequest,
    db: DatabaseSession,
    current_user: CurrentUser,
    ai_service: AISvc,
    token_service: TokenSvc,
):
    """Сгенерировать идею и сохранить в базу."""
    # Генерируем идею
    try:
        generated = await ai_service.generate_idea_from_problem(data.problem)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"AI service unavailable. Configure LLM_API_KEY. Error: {str(e)}"
        )

    # Сохраняем в базу
    idea = Idea(
        title=generated.get("title", "Untitled"),
        problem=generated.get("problem", data.problem),
        solution=generated.get("solution", ""),
        category=generated.get("category", "Other"),
        author_id=current_user.id,
        ai_generated=True,
        status="voting",
    )
    db.add(idea)
    await db.flush()

    # Начисляем токены (меньше чем за свою идею)
    await token_service.award_tokens(
        user_id=current_user.id,
        action=TokenAction.IDEA_CREATED,
        idea_id=idea.id,
        custom_amount=50,  # Половина за AI-сгенерированную
    )

    return {
        "id": str(idea.id),
        "title": idea.title,
        "problem": idea.problem,
        "solution": idea.solution,
        "category": idea.category,
        "tokens_earned": 50,
    }


# === CRUD для DiscoveredProblem (для scheduler) ===


@router.post("/problems", status_code=status.HTTP_201_CREATED)
async def create_problem(
    data: ProblemCreate,
    db: DatabaseSession,
    current_user: CurrentUser,
):
    """Создать запись о найденной проблеме (для scheduler)."""
    # Используем простую таблицу - пока что сохраняем в ideas как черновик
    # В production здесь была бы отдельная таблица discovered_problems
    problem_record = {
        "title": data.title,
        "description": data.description,
        "source": data.source,
        "url": data.url,
        "severity": data.severity,
        "category": data.category,
        "status": "new",
    }
    
    # Для MVP сохраняем как идею-черновик
    idea = Idea(
        title=data.title,
        description=data.description,
        category=data.category or "other",
        author_id=current_user.id,
        ai_generated=True,
        status="draft",  # Черновик — не показывается в ленте
    )
    db.add(idea)
    await db.flush()
    
    return {
        "id": str(idea.id),
        "title": data.title,
        "status": "created",
    }


@router.patch("/problems/{problem_id}")
async def update_problem_status(
    problem_id: UUID,
    data: ProblemUpdate,
    db: DatabaseSession,
    current_user: CurrentUser,
):
    """Обновить статус проблемы."""
    stmt = select(Idea).where(Idea.id == problem_id)
    result = await db.execute(stmt)
    idea = result.scalar_one_or_none()
    
    if not idea:
        raise HTTPException(status_code=404, detail="Problem not found")
    
    if data.status:
        idea.status = data.status
    
    await db.flush()
    
    return {"id": str(idea.id), "status": idea.status}


@router.post("/cleanup")
async def cleanup_old_problems(
    db: DatabaseSession,
    current_user: CurrentUser,
    days: int = Query(default=30, ge=1, le=365),
):
    """Удалить старые проблемы (для scheduler maintenance)."""
    from datetime import datetime, timedelta
    from sqlalchemy import delete
    
    cutoff_date = datetime.utcnow() - timedelta(days=days)
    
    # Удаляем старые draft идеи (AI-сгенерированные проблемы)
    stmt = delete(Idea).where(
        Idea.status == "draft",
        Idea.ai_generated == True,
        Idea.created_at < cutoff_date,
    )
    result = await db.execute(stmt)
    await db.commit()
    
    return {"deleted": result.rowcount}
