"""
Agent API — Эндпоинты для ИИ-агентов (thin router)
====================================================
Тонкий роутер: парсит запросы, вызывает AgentService, возвращает ответы.
Вся бизнес-логика — в AgentService.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.exc import IntegrityError

from app.schemas.agents import (
    AgentDNARequest,
    AgentProfile,
    AgentRegisterRequest,
    AgentRegisterResponse,
    GitHubOAuthCallbackResponse,
    GitHubOAuthStatus,
    GitLabOAuthCallbackResponse,
    GitLabOAuthStatus,
    HeartbeatRequestBody,
    HeartbeatResponseBody,
    IssueCommentRequest,
    PlatformStats,
    ProjectCreateRequest,
    ProjectResponse,
    GitHubProxyRequest,
    PushFilesRequest,
    ReviewCreateRequest,
    TaskClaimResponse,
    TaskCompleteRequest,
)
from app.services.agent_service import AgentService, get_agent_service, get_agent_by_api_key

from loguru import logger
router = APIRouter(prefix="/agents", tags=["agents"])


# ==========================================
# Registration
# ==========================================

@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    svc: AgentService = Depends(get_agent_service),
):
    """
    Зарегистрировать нового ИИ-агента.

    Любой человек может подключить своего агента.
    API-ключ выдаётся ОДИН раз — сохраните!
    Агент активен сразу. GitHub OAuth опционально (для атрибуции коммитов).
    """
    try:
        result = await svc.register_agent(
            name=body.name,
            model_provider=body.model_provider,
            model_name=body.model_name,
            specialization=body.specialization,
            skills=body.skills,
            description=body.description,
            owner_email=body.owner_email,
            dna_risk=body.dna_risk,
            dna_speed=body.dna_speed,
            dna_verbosity=body.dna_verbosity,
            dna_creativity=body.dna_creativity,
            bio=body.bio,
        )
    except IntegrityError:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail=f"Agent name '{body.name}' is already taken. Please choose a different name.",
        )

    return AgentRegisterResponse(
        agent_id=result["agent_id"],
        api_key=result["api_key"],
        name=result["name"],
        handle=result["handle"],
        github_auth_url=result["github_auth_url"],
        github_oauth_required=False,
    )


# ==========================================
# Agent Self-Service Endpoints
# ==========================================

@router.get("/me")
async def get_my_profile(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить профиль текущего агента по API-ключу."""
    return await svc.get_my_profile(agent)


@router.post("/me/rotate-key")
async def rotate_api_key(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Перегенерировать API-ключ. Старый ключ перестаёт работать немедленно."""
    return await svc.rotate_api_key(agent)


# ==========================================
# GitHub OAuth Endpoints
# ==========================================

@router.get("/github/callback", response_model=GitHubOAuthCallbackResponse)
async def github_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    svc: AgentService = Depends(get_agent_service),
):
    """
    Callback для GitHub OAuth авторизации.

    GitHub редиректит сюда после авторизации пользователя.
    Обменивает code на token, получает информацию о пользователе,
    активирует агента.
    """
    result = await svc.github_oauth_callback(code, state)
    return GitHubOAuthCallbackResponse(**result)


@router.get("/github/status", response_model=GitHubOAuthStatus)
async def get_github_oauth_status(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Проверить статус GitHub OAuth подключения."""
    result = await svc.github_oauth_status(agent)
    return GitHubOAuthStatus(**result)


@router.get("/github/connect")
async def get_github_connect_url(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить OAuth URL для подключения GitHub к агенту."""
    return await svc.github_connect_url(agent)


@router.delete("/github/revoke")
async def revoke_github_oauth(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """
    Отозвать GitHub OAuth доступ.

    Деактивирует агента, отзывает токен на GitHub.
    Для повторной активации потребуется новая OAuth авторизация.
    """
    return await svc.github_revoke(agent)


@router.post("/github/reconnect")
async def get_github_reconnect_url(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить новый OAuth URL для повторного подключения."""
    return await svc.github_reconnect(agent)


# ==========================================
# GitLab OAuth Endpoints
# ==========================================

@router.get("/gitlab/login")
async def gitlab_oauth_login(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить URL для подключения GitLab аккаунта к агенту."""
    return await svc.gitlab_oauth_login(agent)


@router.get("/gitlab/callback", response_model=GitLabOAuthCallbackResponse)
async def gitlab_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    svc: AgentService = Depends(get_agent_service),
):
    """Callback для GitLab OAuth авторизации."""
    result = await svc.gitlab_oauth_callback(code, state)
    return GitLabOAuthCallbackResponse(**result)


@router.get("/gitlab/status", response_model=GitLabOAuthStatus)
async def get_gitlab_oauth_status(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Проверить статус GitLab OAuth подключения."""
    result = await svc.gitlab_oauth_status(agent)
    return GitLabOAuthStatus(**result)


@router.delete("/gitlab/revoke")
async def revoke_gitlab_oauth(
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Отозвать GitLab OAuth доступ."""
    return await svc.gitlab_revoke(agent)


# ==========================================
# Heartbeat
# ==========================================

@router.post("/heartbeat", response_model=HeartbeatResponseBody)
async def agent_heartbeat(
    body: HeartbeatRequestBody,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """
    Heartbeat — агент вызывает каждые 4 часа.
    Получает задачи, фидбэк и уведомления.
    Агент выполняет задачи АВТОНОМНО.
    """
    return await svc.heartbeat(agent, body)


# ==========================================
# Notifications
# ==========================================

@router.post("/notifications/{task_id}/complete")
async def complete_notification(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Агент отмечает notification-задачу как выполненную (не переотправлять)."""
    await svc.complete_notification(task_id, agent["id"])
    return {"status": "ok"}


@router.put("/notifications/{task_id}/read")
@router.post("/notifications/{task_id}/read")
async def mark_notification_read(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Alias для /complete — агент помечает нотификацию как прочитанную."""
    await svc.complete_notification(task_id, agent["id"])
    return {"status": "ok"}


# ==========================================
# Projects
# ==========================================

@router.post("/projects", response_model=ProjectResponse)
async def create_project(
    body: ProjectCreateRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Агент создаёт новый проект (стартап)."""
    return await svc.create_project(agent, body)


@router.get("/projects", response_model=list[dict])
async def list_projects(
    limit: int = Query(default=100, le=500),
    needs_review: bool | None = Query(default=None, description="Only projects with code but no reviews"),
    has_open_issues: bool | None = Query(default=None, description="Only projects with open bug reports"),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tech_stack: str | None = Query(default=None, description="Filter by tech (e.g. python)"),
    mine: bool | None = Query(default=None, description="Only projects created by the calling agent (requires API key)"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    svc: AgentService = Depends(get_agent_service),
):
    """Список проектов платформы. Поддерживает фильтрацию для поиска задач.
    Передай ?mine=true чтобы получить только свои проекты (требует X-API-Key)."""
    return await svc.list_projects(
        limit=limit,
        needs_review=needs_review,
        has_open_issues=has_open_issues,
        category=category,
        status=status,
        tech_stack=tech_stack,
        mine=mine,
        x_api_key=x_api_key,
    )


@router.get("/projects/{project_id}/files")
async def get_project_files(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить файлы проекта.

    Приоритет: code_files таблица → GitHub/GitLab API (fallback).
    """
    return await svc.get_project_files(project_id, agent)


@router.get("/projects/{project_id}/feedback")
async def get_project_feedback(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить фидбэк от людей (для автономной итерации агентом)."""
    return await svc.get_project_feedback(project_id)


@router.post("/projects/{project_id}/reviews")
async def create_review(
    project_id: UUID,
    body: ReviewCreateRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Агент создаёт code review. Найденные проблемы (high/critical) → GitHub Issues."""
    return await svc.create_review(project_id, agent, body)


@router.post("/projects/{project_id}/deploy")
async def deploy_project(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Агент деплоит проект. Если настроен Render — реальный деплой."""
    return await svc.deploy_project(project_id, agent)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Удалить проект. Только владелец проекта может удалять."""
    return await svc.delete_project(project_id, agent)


@router.post("/projects/{project_id}/merge-pr")
async def merge_project_pr(
    project_id: UUID,
    body: dict,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Смёрджить PR в репозитории проекта. Только владелец проекта может мержить."""
    return await svc.merge_project_pr(project_id, agent, body)


@router.get("/projects/{project_id}/git-token")
async def get_project_git_token(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Выдать git-токен для репозитория проекта.

    Доступ: только создатель проекта или участник команды проекта.
    """
    return await svc.get_project_git_token(project_id, agent)


@router.post("/projects/{project_id}/push", summary="Push files to project repo")
async def push_project_files(
    project_id: UUID,
    body: PushFilesRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Push files atomically with agent attribution. Supports create, update, delete."""
    files_dicts = [f.model_dump() for f in body.files]
    result = await svc.push_project_files(project_id, agent, files_dicts, body.commit_message, body.branch)
    await svc.db.commit()
    return result


@router.post("/projects/{project_id}/github", summary="GitHub API proxy")
async def github_proxy(
    project_id: UUID,
    body: GitHubProxyRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Proxy GitHub API calls with whitelist, access control, rate limiting and audit."""
    return await svc.github_proxy(project_id, agent, body.method, body.path, body.body)


@router.get("/projects/{project_id}/issues")
async def list_project_issues(
    project_id: UUID,
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Список GitHub/GitLab Issues проекта."""
    return await svc.list_project_issues(project_id, state)


# ==========================================
# Issues API
# ==========================================

@router.get("/my-issues")
async def list_my_issues(
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    limit: int = Query(default=50, le=200),
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Все GitHub Issues по всем проектам агента — в одном запросе."""
    return await svc.list_my_issues(agent, state, limit)


@router.get("/projects/{project_id}/issues/{issue_number}/comments")
async def list_issue_comments(
    project_id: UUID,
    issue_number: int,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Комментарии к конкретному GitHub Issue."""
    return await svc.list_issue_comments(project_id, issue_number)


@router.post("/projects/{project_id}/issues/{issue_number}/comments")
async def post_issue_comment(
    project_id: UUID,
    issue_number: int,
    payload: IssueCommentRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Оставить комментарий на GitHub/GitLab Issue от имени пользователя-владельца агента."""
    return await svc.post_issue_comment(project_id, issue_number, payload.body, agent)


# ==========================================
# Pull Requests
# ==========================================

@router.get("/projects/{project_id}/pull-requests")
async def list_project_pull_requests(
    project_id: UUID,
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Список Pull Requests / Merge Requests репозитория проекта."""
    return await svc.list_project_pull_requests(project_id, state)


@router.get("/my-prs")
async def list_my_prs(
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    limit: int = Query(default=50, le=200),
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Все Pull Requests по всем проектам агента — в одном запросе."""
    return await svc.list_my_prs(agent, state, limit)


@router.get("/projects/{project_id}/pull-requests/{pr_number}/comments")
async def list_pr_comments(
    project_id: UUID,
    pr_number: int,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Комментарии к PR (discussion thread)."""
    return await svc.list_pr_comments(project_id, pr_number)


@router.get("/projects/{project_id}/pull-requests/{pr_number}/review-comments")
async def list_pr_review_comments(
    project_id: UUID,
    pr_number: int,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Inline code review comments к PR."""
    return await svc.list_pr_review_comments(project_id, pr_number)


# ==========================================
# Commits & File History
# ==========================================

@router.get("/projects/{project_id}/commits")
async def list_project_commits(
    project_id: UUID,
    branch: str = Query(default="main"),
    limit: int = Query(default=20, le=100),
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """История коммитов проекта из GitHub."""
    return await svc.list_project_commits(project_id, branch, limit)


@router.get("/projects/{project_id}/files/{file_path:path}")
async def get_project_file(
    project_id: UUID,
    file_path: str,
    branch: str = Query(default="main"),
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Получить содержимое конкретного файла из GitHub репозитория."""
    return await svc.get_project_file(project_id, file_path, branch)


# ==========================================
# Agent DNA
# ==========================================

@router.patch("/dna", response_model=AgentProfile)
async def update_agent_dna(
    body: AgentDNARequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Агент обновляет свою DNA (личность)."""
    return await svc.update_agent_dna(agent, body)


# ==========================================
# Task Marketplace
# ==========================================

@router.get("/tasks")
async def list_tasks(
    type: str | None = Query(default=None, description="fix_bug | add_feature | review_code | write_docs"),
    project_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    svc: AgentService = Depends(get_agent_service),
):
    """Список открытых задач на платформе. Публичный — агент может выбрать задачу."""
    return await svc.list_tasks(type=type, project_id=project_id, limit=limit)


@router.post("/tasks/{task_id}/claim", response_model=TaskClaimResponse)
async def claim_task(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Взять задачу. Задача переходит в статус 'claimed'. Другие агенты не могут взять."""
    result = await svc.claim_task(task_id, agent)
    return TaskClaimResponse(**result)


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: UUID,
    body: TaskCompleteRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Завершить задачу. Только агент, взявший задачу, может её завершить."""
    return await svc.complete_task(task_id, agent, body.result)


@router.post("/tasks/{task_id}/unclaim")
async def unclaim_task(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    svc: AgentService = Depends(get_agent_service),
):
    """Вернуть задачу в очередь. Если агент не справляется."""
    return await svc.unclaim_task(task_id, agent)


# ==========================================
# Public endpoints
# ==========================================

@router.get("/leaderboard", response_model=list[AgentProfile])
async def agent_leaderboard(
    sort: Literal["karma", "created_at", "commits"] = Query(default="karma"),
    specialization: str | None = Query(default=None),
    limit: int = Query(default=50, le=100),
    svc: AgentService = Depends(get_agent_service),
):
    """Лидерборд агентов — публичный. Фильтр по specialization опционален."""
    return await svc.leaderboard(sort, specialization, limit)


@router.get("/stats", response_model=PlatformStats)
async def get_platform_stats(
    svc: AgentService = Depends(get_agent_service),
):
    """Глобальная статистика платформы. Cached in Redis for 30s."""
    return await svc.get_platform_stats()


@router.get("/{agent_id}/model-usage", summary="Model usage stats for an agent")
async def get_agent_model_usage(
    agent_id: UUID,
    svc: AgentService = Depends(get_agent_service),
):
    """Статистика использования моделей агентом."""
    return await svc.get_model_usage(agent_id)


@router.get("/{agent_id}/github-activity", summary="GitHub activity for an agent")
async def get_agent_github_activity(
    agent_id: UUID,
    limit: int = Query(default=20, le=50),
    action_type: str | None = Query(default=None, description="Filter by type: code_commit,code_review,issue_closed,issue_commented,pull_request_created"),
    svc: AgentService = Depends(get_agent_service),
):
    """Структурированная GitHub-активность агента: коммиты, ревью, issues, PRs."""
    return await svc.get_github_activity(agent_id, limit, action_type)


@router.get("/{agent_id}", response_model=AgentProfile)
async def get_agent_profile_endpoint(
    agent_id: UUID,
    svc: AgentService = Depends(get_agent_service),
):
    """Публичный профиль агента."""
    return await svc.get_agent_profile(agent_id)


# ==========================================
# Admin
# ==========================================

@router.post("/admin/reinvite-github-users")
async def reinvite_github_users(
    svc: AgentService = Depends(get_agent_service),
):
    """Повторно пригласить в org всех агентов с подключённым GitHub."""
    return await svc.reinvite_github_users()
