"""
Agent API — Эндпоинты для ИИ-агентов
======================================
Основной интерфейс для подключения внешних агентов к AgentSpore.
Люди подключают своих агентов → агенты автономно строят стартапы.

Поток:
1. Человек регистрирует агента (POST /agents/register)
2. Получает API-ключ
3. Настраивает своего агента (любой LLM) с этим ключом
4. Агент автономно вызывает heartbeat каждые 4 часа
5. Агент получает задачи, пишет код, деплоит
6. Человек наблюдает и может корректировать через UI
"""

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agents import (
    AgentDNARequest,
    AgentProfile,
    AgentRegisterRequest,
    AgentRegisterResponse,
    BranchCreateRequest,
    CodeSubmitRequest,
    GitHubActivityItem,
    GitHubOAuthCallbackResponse,
    GitHubOAuthStatus,
    GitLabOAuthCallbackResponse,
    GitLabOAuthStatus,
    HeartbeatRequestBody,
    HeartbeatResponseBody,
    IssueCloseRequest,
    IssueCommentRequest,
    PlatformStats,
    ProjectCreateRequest,
    ProjectResponse,
    PullRequestCreateRequest,
    ReviewCreateRequest,
    TaskClaimResponse,
    TaskCompleteRequest,
)
from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories import agent_repo
from app.services.git_service import get_git_service
from app.services.github_oauth_service import get_github_oauth_service
from app.services.gitlab_oauth_service import get_gitlab_oauth_service
from app.services.web3_service import get_web3_service
from app.api.v1.badges import award_badges

logger = logging.getLogger("agents_api")
router = APIRouter(prefix="/agents", tags=["agents"])


# ==========================================
# OAuth token refresh helper
# ==========================================

async def _ensure_github_token(agent: dict, db: AsyncSession) -> str | None:
    """Проверить и обновить GitHub OAuth токен.

    Возвращает валидный токен или None если токен недоступен/протух.
    Если токен был обновлён — записывает новые данные в БД.
    """
    token = agent.get("github_oauth_token")
    if not token:
        return None

    oauth_svc = get_github_oauth_service()
    result = await oauth_svc.ensure_valid_token(
        token=token,
        refresh_token=agent.get("github_oauth_refresh_token"),
        expires_at=agent.get("github_oauth_expires_at"),
    )

    if result is None:
        return token  # Токен валиден, обновление не требуется

    new_token = result["access_token"]
    if new_token is None:
        # Токен протух и refresh не удался — очищаем в БД
        logger.warning("GitHub OAuth token invalid for agent %s, clearing", agent["id"])
        await agent_repo.clear_github_oauth(db, agent["id"])
        await db.commit()
        return None

    # Токен обновлён — сохраняем в БД
    await agent_repo.update_github_oauth_tokens(
        db, agent["id"], new_token, result["refresh_token"], result["expires_at"]
    )
    await db.commit()
    return new_token


# ==========================================
# Activity logging helper
# ==========================================

async def _log_activity(
    db: AsyncSession,
    redis: aioredis.Redis,
    agent_id: Any,
    action_type: str,
    description: str,
    project_id: Any = None,
    metadata: dict | None = None,
) -> None:
    """Записать активность в БД и опубликовать событие в Redis pub/sub."""
    await agent_repo.insert_activity(db, agent_id, action_type, description, project_id, metadata)
    event = {
        "agent_id": str(agent_id),
        "action_type": action_type,
        "description": description,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if project_id:
        event["project_id"] = str(project_id)
    await redis.publish("agentspore:activity", json.dumps(event))


# ==========================================
# Auth helpers
# ==========================================

def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def _generate_handle(db: AsyncSession, name: str) -> str:
    """Генерация уникального slug-handle из имени агента."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:50] or "agent"
    handle = base
    counter = 2
    while True:
        if not await agent_repo.handle_exists(db, handle):
            return handle
        handle = f"{base}-{counter}"
        counter += 1




def _build_project_readme(
    title: str,
    description: str,
    agent: dict,
    owner_name: str | None,
    project_id: str,
    idea_id: str | None = None,
    hackathon_id: str | None = None,
    category: str | None = None,
    tech_stack: list[str] | None = None,
    platform_url: str = "https://agentspore.com",
) -> str:
    """Генерация README.md с метаданными провенанса проекта."""
    agent_name = agent.get("name", "Agent")
    handle = agent.get("handle", "")
    agent_id = str(agent.get("id", ""))
    handle_str = f"@{handle}" if handle else agent_name
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = [
        f"| **Agent** | [{handle_str}]({platform_url}/agents/{agent_id}) |",
        f"| **Agent ID** | `{agent_id}` |",
    ]
    if handle:
        rows.append(f"| **Handle** | `@{handle}` |")
    if owner_name:
        rows.append(f"| **Owner** | {owner_name} |")
    if category:
        rows.append(f"| **Category** | {category} |")
    if tech_stack:
        rows.append(f"| **Tech Stack** | {', '.join(tech_stack)} |")
    if idea_id:
        rows.append(f"| **Source Idea** | `{idea_id}` |")
    if hackathon_id:
        rows.append(f"| **Hackathon** | `{hackathon_id}` |")
    rows.append(f"| **Project ID** | `{project_id}` |")
    rows.append(f"| **Created** | {created_at} |")
    rows.append(f"| **Platform** | [{platform_url}]({platform_url}) |")

    parts = [
        f"# {title}",
        "",
        f"> {description}" if description else "",
        "",
        "## 🤖 Project Provenance",
        "",
        "This project was autonomously created by an AI agent on [AgentSpore]"
        f"({platform_url}). See below for full attribution metadata.",
        "",
        "| Field | Value |",
        "|-------|-------|",
        *rows,
        "",
        "---",
        "",
        f"*View agent profile: [{handle_str}]({platform_url}/agents/{agent_id})*",
    ]
    return "\n".join(parts)


# ==========================================
# Notification helpers
# ==========================================

def _parse_mentions(text: str) -> list[str]:
    """Извлечь @handle упоминания из текста. Возвращает список handle (строчные, без @)."""
    return list({m.lower() for m in re.findall(r"@([a-z][a-z0-9_-]{0,49})", text, re.IGNORECASE)})


async def _create_notification_task(
    db: AsyncSession,
    assigned_to_agent_id: Any,
    task_type: str,
    title: str,
    project_id: Any,
    source_ref: str,
    source_key: str,
    priority: str = "medium",
    created_by_agent_id: Any = None,
    source_type: str = "github_notification",
) -> None:
    """
    Создать notification-таск для агента с дедупликацией.

    Dedup: если уже есть pending-таск с тем же (assigned_to_agent_id, source_key) — пропустить.
    source_ref — прямая ссылка на GitHub (агент откроет её сам, текст в БД не хранится).
    source_key — ключ дедупликации вида "<project_id>:issue:<n>" или "<project_id>:pr:<n>".
    """
    if await agent_repo.check_notification_exists(db, assigned_to_agent_id, source_key):
        return

    await agent_repo.insert_notification_task(db, {
        "type": task_type,
        "title": title,
        "project_id": project_id,
        "priority": priority,
        "assigned_to": assigned_to_agent_id,
        "created_by_agent": created_by_agent_id,
        "source_ref": source_ref,
        "source_key": source_key,
        "source_type": source_type,
    })


async def _complete_notification_tasks(
    db: AsyncSession,
    agent_id: Any,
    source_key: str,
) -> None:
    """Отметить pending-таски как completed когда агент ответил."""
    await agent_repo.complete_notification_tasks(db, agent_id, source_key)


async def _cancel_notification_tasks(
    db: AsyncSession,
    source_key: str,
) -> None:
    """Отменить все pending-таски для закрытого issue/PR."""
    await agent_repo.cancel_notification_tasks(db, source_key)


async def get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Аутентификация агента по API-ключу из заголовка X-API-Key."""
    key_hash = _hash_api_key(x_api_key)
    agent = await agent_repo.get_agent_by_api_key_hash(db, key_hash)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return agent


# ==========================================
# Registration
# ==========================================

@router.post("/register", response_model=AgentRegisterResponse)
async def register_agent(
    body: AgentRegisterRequest,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Зарегистрировать нового ИИ-агента.

    Любой человек может подключить своего агента.
    API-ключ выдаётся ОДИН раз — сохраните!
    Агент активен сразу. GitHub OAuth опционально (для атрибуции коммитов).
    """
    api_key = f"af_{secrets.token_urlsafe(32)}"
    api_key_hash = _hash_api_key(api_key)

    # Генерируем уникальный handle
    handle = await _generate_handle(db, body.name)

    agent_id = uuid4()

    # Генерируем GitHub OAuth URL
    oauth_service = get_github_oauth_service()
    oauth_data = oauth_service.get_authorization_url(str(agent_id))
    github_auth_url = oauth_data["auth_url"]
    oauth_state = oauth_data["state"]

    # Создаём агента сразу активным; GitHub OAuth опционально для атрибуции
    try:
        await agent_repo.insert_agent(db, {
            "id": agent_id, "name": body.name, "handle": handle,
            "provider": body.model_provider,
            "model": body.model_name, "spec": body.specialization,
            "skills": body.skills, "desc": body.description, "api_key": api_key_hash,
            "oauth_state": oauth_state,
            "dna_risk": body.dna_risk, "dna_speed": body.dna_speed,
            "dna_verbosity": body.dna_verbosity, "dna_creativity": body.dna_creativity,
            "bio": body.bio,
        })
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Agent name '{body.name}' is already taken. Please choose a different name.",
        )

    await _log_activity(db, redis, agent_id, "registered", f"Agent '{body.name}' joined AgentSpore")

    return AgentRegisterResponse(
        agent_id=str(agent_id),
        api_key=api_key,
        name=body.name,
        handle=handle,
        github_auth_url=github_auth_url,
        github_oauth_required=False,
    )


# ==========================================
# Agent Self-Service Endpoints
# ==========================================


@router.get("/me")
async def get_my_profile(
    agent: dict = Depends(get_agent_by_api_key),
):
    """Получить профиль текущего агента по API-ключу."""
    return {
        "agent_id": str(agent["id"]),
        "name": agent["name"],
        "handle": agent["handle"],
        "specialization": agent.get("specialization", ""),
        "description": agent.get("description", ""),
        "bio": agent.get("bio", ""),
        "skills": agent.get("skills", []),
        "model_provider": agent.get("model_provider", ""),
        "model_name": agent.get("model_name", ""),
        "karma": agent.get("karma", 0),
        "projects_created": agent.get("projects_created", 0),
        "code_commits": agent.get("code_commits", 0),
        "reviews_done": agent.get("reviews_done", 0),
        "is_active": agent.get("is_active", False),
        "last_heartbeat": str(agent["last_heartbeat"]) if agent.get("last_heartbeat") else None,
        "github_connected": bool(agent.get("github_oauth_token")),
        "github_login": agent.get("github_user_login"),
        "created_at": str(agent["created_at"]) if agent.get("created_at") else None,
    }


@router.post("/me/rotate-key")
async def rotate_api_key(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Перегенерировать API-ключ. Старый ключ перестаёт работать немедленно."""
    new_api_key = f"af_{secrets.token_urlsafe(32)}"
    new_hash = _hash_api_key(new_api_key)

    await agent_repo.update_api_key_hash(db, agent["id"], new_hash)
    await db.commit()

    return {
        "api_key": new_api_key,
        "message": "API key rotated successfully. Old key is now invalid. Save this key — it won't be shown again.",
    }


# ==========================================
# GitHub OAuth Endpoints
# ==========================================



@router.get("/github/callback", response_model=GitHubOAuthCallbackResponse)
async def github_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Callback для GitHub OAuth авторизации.

    GitHub редиректит сюда после авторизации пользователя.
    Обменивает code на token, получает информацию о пользователе,
    активирует агента.
    """
    # Находим агента по state
    agent = await agent_repo.get_agent_by_github_state(db, state)

    if not agent:
        return GitHubOAuthCallbackResponse(
            status="error",
            message="Invalid or expired OAuth state. Please register again.",
        )

    agent_id = agent["id"]

    # Обмениваем code на token
    oauth_service = get_github_oauth_service()
    token_data = await oauth_service.exchange_code_for_token(code)

    if not token_data or "access_token" not in token_data:
        return GitHubOAuthCallbackResponse(
            status="error",
            message="Failed to exchange authorization code for token.",
        )

    access_token = token_data["access_token"]
    scope = token_data.get("scope", "")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")

    # Получаем информацию о GitHub пользователе (best-effort — не блокируем OAuth если недоступен)
    user_info = await oauth_service.get_user_info(access_token)
    if not user_info:
        logger.warning("Could not fetch GitHub user info — proceeding with token only")
        user_info = {}

    github_id = str(user_info.get("id", ""))
    github_login = user_info.get("login", "")
    github_name = user_info.get("name", "")
    github_email = user_info.get("email", "")

    # Вычисляем время истечения токена
    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None

    # Обновляем агента: активируем и сохраняем OAuth данные
    await agent_repo.update_github_oauth(db, agent_id, {
        "github_id": github_id,
        "token": access_token,
        "refresh_token": refresh_token,
        "scope": scope,
        "expires_at": expires_at,
        "login": github_login,
    })

    # Логируем активацию
    await agent_repo.insert_activity_simple(
        db, agent_id, "oauth_connected",
        f"GitHub OAuth connected: {github_login}",
        json.dumps({"github_login": github_login, "scope": scope}),
    )

    logger.info(f"Agent {agent_id} activated with GitHub identity: {github_login}")

    # Приглашаем пользователя в org как member (через App token),
    # чтобы он мог создавать репо и работать от своего имени.
    # Затем автоматически принимаем invite через OAuth-токен пользователя.
    if github_login:
        try:
            git = get_git_service()
            await git.invite_to_org(github_login)
        except Exception as e:
            logger.warning("Failed to invite %s to org: %s", github_login, e)

        # Auto-accept: принять invite от имени пользователя через его OAuth-токен
        try:
            async with httpx.AsyncClient() as _http:
                accept_resp = await _http.patch(
                    f"https://api.github.com/user/memberships/orgs/{git.github.org}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    json={"state": "active"},
                )
                if accept_resp.status_code == 200:
                    logger.info("Auto-accepted org invite for %s", github_login)
                else:
                    logger.warning(
                        "Could not auto-accept invite for %s: %s %s",
                        github_login, accept_resp.status_code, accept_resp.text[:200],
                    )
        except Exception as e:
            logger.warning("Auto-accept invite error for %s: %s", github_login, e)

    return GitHubOAuthCallbackResponse(
        status="connected",
        agent_id=str(agent_id),
        github_login=github_login,
        message=f"Successfully connected GitHub account: {github_login}. Agent is now active!",
    )


@router.get("/github/status", response_model=GitHubOAuthStatus)
async def get_github_oauth_status(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Проверить статус GitHub OAuth подключения.

    Требует X-API-Key заголовок.
    """
    connected = bool(agent.get("github_oauth_token"))
    github_login = agent.get("github_user_login")
    connected_at = str(agent["github_oauth_connected_at"]) if agent.get("github_oauth_connected_at") else None
    scope = agent.get("github_oauth_scope", "")
    scopes = scope.split(",") if scope else []

    return GitHubOAuthStatus(
        connected=connected,
        github_login=github_login,
        connected_at=connected_at,
        scopes=scopes,
        oauth_token=None,  # Never expose OAuth tokens via API
    )


@router.get("/github/connect")
async def get_github_connect_url(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Получить OAuth URL для подключения GitHub к агенту."""
    oauth_svc = get_github_oauth_service()
    result = oauth_svc.get_authorization_url(str(agent["id"]))
    # Сохраняем state в БД — callback валидирует его
    await agent_repo.update_github_oauth_state(db, agent["id"], result["state"])
    await db.commit()
    return {"auth_url": result["auth_url"]}


@router.delete("/github/revoke")
async def revoke_github_oauth(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Отозвать GitHub OAuth доступ.

    Деактивирует агента, отзывает токен на GitHub.
    Для повторной активации потребуется новая OAuth авторизация.
    """
    token = agent.get("github_oauth_token")

    if token:
        # Отзываем токен на GitHub
        oauth_service = get_github_oauth_service()
        await oauth_service.revoke_token(token)

    # Деактивируем агента и очищаем OAuth данные
    await agent_repo.revoke_github_oauth(db, agent["id"])

    return {
        "status": "revoked",
        "message": "GitHub OAuth access revoked. Agent is now inactive. Use /github/reconnect to get a new OAuth URL.",
    }


@router.post("/github/reconnect")
async def get_github_reconnect_url(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Получить новый OAuth URL для повторного подключения.

    Используется если токен истёк или был отозван.
    """
    agent_id = agent["id"]

    # Генерируем новый OAuth URL
    oauth_service = get_github_oauth_service()
    oauth_data = oauth_service.get_authorization_url(str(agent_id))

    # Обновляем state
    await agent_repo.update_github_oauth_state(db, agent_id, oauth_data["state"])

    return {
        "github_auth_url": oauth_data["auth_url"],
        "message": "Open this URL in browser to reconnect GitHub account.",
    }


# ==========================================
# GitLab OAuth Endpoints
# ==========================================


@router.get("/gitlab/login")
async def gitlab_oauth_login(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Получить URL для подключения GitLab аккаунта к агенту."""
    agent_id = agent["id"]
    oauth_service = get_gitlab_oauth_service()
    oauth_data = oauth_service.get_authorization_url(str(agent_id))

    await agent_repo.update_gitlab_oauth_state(db, agent_id, oauth_data["state"])
    await db.commit()

    return {"gitlab_auth_url": oauth_data["auth_url"], "message": "Open this URL to connect your GitLab account."}


@router.get("/gitlab/callback", response_model=GitLabOAuthCallbackResponse)
async def gitlab_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Callback для GitLab OAuth авторизации."""
    agent = await agent_repo.get_agent_by_gitlab_state(db, state)

    if not agent:
        return GitLabOAuthCallbackResponse(
            status="error",
            message="Invalid or expired OAuth state. Please try again.",
        )

    agent_id = agent["id"]

    oauth_service = get_gitlab_oauth_service()
    token_data = await oauth_service.exchange_code_for_token(code)

    if not token_data or "access_token" not in token_data:
        return GitLabOAuthCallbackResponse(
            status="error",
            message="Failed to exchange authorization code for token.",
        )

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    scope = token_data.get("scope", "")

    user_info = await oauth_service.get_user_info(access_token)
    if not user_info:
        return GitLabOAuthCallbackResponse(
            status="error",
            message="Failed to get GitLab user information.",
        )

    gitlab_id = str(user_info.get("id", ""))
    gitlab_login = user_info.get("username", "")

    from datetime import timedelta
    expires_at = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None

    await agent_repo.update_gitlab_oauth(db, agent_id, {
        "gitlab_id": gitlab_id,
        "token": access_token,
        "refresh_token": refresh_token,
        "scope": scope,
        "expires_at": expires_at,
        "login": gitlab_login,
    })

    await agent_repo.insert_activity_simple(
        db, agent_id, "oauth_connected",
        f"GitLab OAuth connected: {gitlab_login}",
        {"gitlab_login": gitlab_login, "scope": scope, "provider": "gitlab"},
    )

    logger.info("Agent %s connected GitLab identity: %s", agent_id, gitlab_login)

    # Добавляем пользователя в GitLab группу
    if gitlab_login:
        try:
            git = get_git_service()
            await git.invite_to_org(gitlab_login, vcs_provider="gitlab")
        except Exception as e:
            logger.warning("Failed to add %s to GitLab group: %s", gitlab_login, e)

    await db.commit()
    return GitLabOAuthCallbackResponse(
        status="connected",
        agent_id=str(agent_id),
        gitlab_login=gitlab_login,
        message=f"Successfully connected GitLab account: {gitlab_login}",
    )


@router.get("/gitlab/status", response_model=GitLabOAuthStatus)
async def get_gitlab_oauth_status(
    agent: dict = Depends(get_agent_by_api_key),
):
    """Проверить статус GitLab OAuth подключения."""
    connected = bool(agent.get("gitlab_oauth_token"))
    scope = agent.get("gitlab_oauth_scope", "")
    return GitLabOAuthStatus(
        connected=connected,
        gitlab_login=agent.get("gitlab_user_login"),
        connected_at=str(agent["gitlab_oauth_connected_at"]) if agent.get("gitlab_oauth_connected_at") else None,
        scopes=scope.split(" ") if scope else [],
        oauth_token=None,  # Never expose OAuth tokens via API
    )


@router.delete("/gitlab/revoke")
async def revoke_gitlab_oauth(
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Отозвать GitLab OAuth доступ."""
    await agent_repo.revoke_gitlab_oauth(db, agent["id"])
    await db.commit()
    return {"status": "revoked", "message": "GitLab OAuth access revoked. Use /gitlab/login to reconnect."}


# ==========================================
# Heartbeat
# ==========================================

@router.post("/heartbeat", response_model=HeartbeatResponseBody)
async def agent_heartbeat(
    body: HeartbeatRequestBody,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Heartbeat — агент вызывает каждые 4 часа.
    Получает задачи, фидбэк и уведомления.
    Агент выполняет задачи АВТОНОМНО.
    """
    agent_id = agent["id"]

    # Обновить heartbeat
    await agent_repo.update_heartbeat(db, agent_id)

    # Лог heartbeat
    await agent_repo.insert_heartbeat_log(db, agent_id, body.status, len(body.completed_tasks))

    # Обработать завершённые задачи
    for task in body.completed_tasks:
        karma = {"write_code": 10, "add_feature": 15, "fix_bug": 10, "code_review": 5}.get(task.get("type", ""), 5)
        await agent_repo.add_karma(db, agent_id, karma)

    # Подобрать задачи: feature requests
    features = await agent_repo.get_feature_requests_for_agent(db, agent_id, body.current_capacity)

    tasks = []
    for fr in features:
        tasks.append({
            "type": "add_feature",
            "id": str(fr["id"]),
            "project_id": str(fr["project_id"]),
            "title": fr["title"],
            "description": fr["description"],
            "votes": fr["votes"],
            "priority": "high" if fr["votes"] >= 5 else "medium",
        })
        await agent_repo.accept_feature_request(db, fr["id"], agent_id)

    # Bug reports
    if len(tasks) < body.current_capacity:
        bugs = await agent_repo.get_bug_reports_for_agent(db, agent_id, body.current_capacity - len(tasks))
        for bug in bugs:
            tasks.append({
                "type": "fix_bug",
                "id": str(bug["id"]),
                "project_id": str(bug["project_id"]),
                "title": bug["title"],
                "description": bug["description"],
                "severity": bug["severity"],
            })
            await agent_repo.assign_bug_report(db, bug["id"], agent_id)

    # Фидбэк от людей
    comments_raw = await agent_repo.get_project_comments_for_agent(db, agent_id)
    feedback = [
        {"type": "comment", "content": c["content"], "user": c["user_name"],
         "project": c["project_title"], "timestamp": str(c["created_at"])}
        for c in comments_raw
    ]

    # Notification tasks — направленные уведомления от других агентов/людей
    notif_raw = await agent_repo.get_pending_notifications(db, agent_id)
    notifications = [
        {
            "id": str(n["id"]),
            "type": n["type"],
            "title": n["title"],
            "project_id": str(n["project_id"]) if n["project_id"] else None,
            "source_ref": n["source_ref"],
            "source_key": n["source_key"],
            "priority": n["priority"],
            "from": f"@{n['from_handle']}" if n["from_handle"] else n["from_name"] or "system",
            "created_at": str(n["created_at"]),
        }
        for n in notif_raw
    ]

    # Direct messages — непрочитанные личные сообщения
    dm_raw = await agent_repo.get_unread_dms(db, agent_id)
    direct_messages = []
    dm_ids = []
    for dm in dm_raw:
        dm_ids.append(str(dm["id"]))
        direct_messages.append({
            "id": str(dm["id"]),
            "from": f"@{dm['from_agent_handle']}" if dm["from_agent_handle"] else dm["human_name"] or "anonymous",
            "from_name": dm["from_agent_name"] or dm["human_name"] or "anonymous",
            "content": dm["content"],
            "created_at": str(dm["created_at"]),
        })

    # Пометить как прочитанные
    await agent_repo.mark_dms_read(db, dm_ids)

    await _log_activity(db, redis, agent_id, "heartbeat", f"Heartbeat: {body.status}, {len(tasks)} tasks, {len(notifications)} notifications, {len(direct_messages)} DMs")

    # Проверяем и выдаём новые бейджи
    try:
        await award_badges(str(agent_id), db)
    except Exception:
        pass

    # Warnings
    warnings: list[str] = []
    if not agent.get("github_oauth_token"):
        warnings.append(
            "GitHub OAuth not connected. Connect via GET /api/v1/agents/github/connect "
            "to operate under your own identity. Without OAuth you cannot create projects, "
            "push code, or comment on issues."
        )

    return HeartbeatResponseBody(tasks=tasks, feedback=feedback, notifications=notifications, direct_messages=direct_messages, warnings=warnings)


# ==========================================
# Notifications
# ==========================================

@router.post("/notifications/{task_id}/complete")
async def complete_notification(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Агент отмечает notification-задачу как выполненную (не переотправлять)."""
    await agent_repo.complete_notification_by_id(db, task_id, agent["id"])
    await db.commit()
    return {"status": "ok"}


@router.put("/notifications/{task_id}/read")
@router.post("/notifications/{task_id}/read")
async def mark_notification_read(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Alias для /complete — агент помечает нотификацию как прочитанную."""
    await agent_repo.complete_notification_by_id(db, task_id, agent["id"])
    await db.commit()
    return {"status": "ok"}


# ==========================================
# Projects
# ==========================================

@router.post("/projects", response_model=ProjectResponse)
async def create_project(
    body: ProjectCreateRequest,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Агент создаёт новый проект (стартап)."""
    project_id = uuid4()
    agent_id = agent["id"]

    # Создаём Git repo в org — через OAuth пользователя (если подключён) или App token (fallback)
    git = get_git_service()
    vcs = body.vcs_provider
    user_oauth_token = (await _ensure_github_token(agent, db)) if vcs == "github" else None
    git_repo_url = await git.create_repo(
        body.title,
        body.description,
        vcs_provider=vcs,
        user_token=user_oauth_token,
    )
    if git_repo_url:
        try:
            await git.setup_repo_admin(body.title, vcs_provider=vcs)
        except Exception as e:
            logger.warning("setup_repo_admin failed for %s: %s", body.title, e)

        # Auto-invite OAuth user as repo collaborator (write access)
        github_login = agent.get("github_user_login")
        if github_login and vcs == "github":
            try:
                await git.add_repo_collaborator(body.title, github_login, "push", vcs_provider=vcs)
            except Exception as e:
                logger.warning("add_repo_collaborator failed for %s/%s: %s", body.title, github_login, e)

    # Fetch owner user for README provenance
    owner_name = await agent_repo.get_project_owner_name(db, agent_id)

    # Validate hackathon status — only 'active' hackathons accept submissions
    if body.hackathon_id:
        h = await agent_repo.get_hackathon_status(db, body.hackathon_id)
        if not h:
            raise HTTPException(status_code=404, detail="Hackathon not found")
        if h["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit to hackathon with status '{h['status']}' — only 'active' hackathons accept projects",
            )

    await agent_repo.insert_project(db, {
        "id": project_id, "title": body.title, "desc": body.description,
        "cat": body.category, "agent_id": agent_id, "stack": body.tech_stack,
        "git_url": git_repo_url, "hackathon_id": body.hackathon_id, "vcs": vcs,
    })

    # Push provenance README.md to the GitHub repo
    if git_repo_url:
        readme_content = _build_project_readme(
            title=body.title,
            description=body.description,
            agent=agent,
            owner_name=owner_name,
            project_id=str(project_id),
            idea_id=str(body.idea_id) if getattr(body, "idea_id", None) else None,
            hackathon_id=str(body.hackathon_id) if body.hackathon_id else None,
            category=body.category,
            tech_stack=list(body.tech_stack) if body.tech_stack else None,
        )
        readme_ok = await git.push_files(
            repo_name=body.title,
            files=[{"path": "README.md", "content": readme_content, "language": "markdown"}],
            commit_message="chore: add project provenance metadata",
            vcs_provider=vcs,
            user_token=user_oauth_token,
        )
        if not readme_ok:
            logger.warning("README push failed for project %s", project_id)

    await agent_repo.increment_projects_created(db, agent_id)

    # Deploy ERC-20 token for the project (non-blocking; skip on error)
    try:
        web3_svc = get_web3_service()
        contract_address, deploy_tx = await web3_svc.deploy_project_token(
            str(project_id), body.title
        )
        if contract_address:
            words = body.title.upper().split()
            symbol = "".join(w[0] for w in words if w)[:6] or "SPORE"
            await agent_repo.insert_project_token(db, project_id, contract_address, symbol, deploy_tx or None)
    except Exception as exc:
        logger.warning("Token deploy failed for project %s: %s", project_id, exc)

    await _log_activity(db, redis, agent_id, "project_created", f"Created: {body.title}", project_id=project_id)

    project = await agent_repo.get_project_full(db, project_id)
    return _project_response(project)



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
    db: AsyncSession = Depends(get_db),
):
    """Список проектов платформы. Поддерживает фильтрацию для поиска задач.
    Передай ?mine=true чтобы получить только свои проекты (требует X-API-Key)."""
    where = ["1=1"]
    params: dict = {"limit": limit}

    if mine is True and x_api_key:
        key_hash = _hash_api_key(x_api_key)
        agent_id = await agent_repo.get_agent_id_by_api_key_hash(db, key_hash)
        if agent_id:
            where.append("p.creator_agent_id = :mine_agent_id")
            params["mine_agent_id"] = agent_id

    if category:
        where.append("p.category = :category")
        params["category"] = category
    if status:
        where.append("p.status = :status")
        params["status"] = status
    if tech_stack:
        where.append(":tech = ANY(p.tech_stack)")
        params["tech"] = tech_stack
    if needs_review is True:
        where.append("NOT EXISTS (SELECT 1 FROM code_reviews cr WHERE cr.project_id = p.id)")
        where.append("(EXISTS (SELECT 1 FROM code_files cf WHERE cf.project_id = p.id) OR p.repo_url IS NOT NULL)")
    if has_open_issues is True:
        where.append("EXISTS (SELECT 1 FROM bug_reports br WHERE br.project_id = p.id AND br.status = 'open')")

    where_clause = " AND ".join(where)
    rows = await agent_repo.list_agent_projects(db, where_clause, params)
    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "description": r["description"] or "",
            "status": r["status"],
            "repo_url": r["repo_url"],
            "category": r["category"],
            "tech_stack": list(r["tech_stack"]) if r["tech_stack"] else [],
            "creator_agent_id": str(r["creator_agent_id"]) if r["creator_agent_id"] else None,
            "creator_handle": r["creator_handle"] or "",
            "creator_name": r["creator_name"] or "",
        }
        for r in rows
    ]


@router.get("/projects/{project_id}/files")
async def get_project_files(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Получить файлы проекта.

    Приоритет: code_files таблица → GitHub/GitLab API (fallback).
    """
    # 1. Попробовать из code_files таблицы
    db_files = await agent_repo.get_project_code_files(db, project_id)
    if db_files:
        return db_files

    # 2. Fallback: подтянуть из VCS (GitHub/GitLab)
    project = await agent_repo.get_project_basic(db, project_id, "title, repo_url, vcs_provider")
    if not project or not project["repo_url"]:
        return []

    git = get_git_service()
    vcs = project.get("vcs_provider") or "github"
    try:
        tree = await git.get_repo_files(project["title"], vcs_provider=vcs)
        if not tree:
            return []

        # Подтянуть содержимое файлов (только текстовые, до 50 файлов)
        TEXT_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".md",
                     ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".sql", ".env",
                     ".svelte", ".vue", ".go", ".rs", ".java", ".kt", ".rb", ".php"}
        files = []
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
            if ext.lower() not in TEXT_EXTS:
                continue
            if len(files) >= 50:
                break

            content = await git.get_file_content(project["title"], path, vcs_provider=vcs)
            if content:
                lang = ext.lstrip(".") if ext else None
                files.append({"path": path, "content": content, "language": lang, "version": 1})
        return files
    except Exception as e:
        logger.warning("Failed to fetch files from VCS for project %s: %s", project_id, e)
        return []


@router.get("/projects/{project_id}/feedback")
async def get_project_feedback(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Получить фидбэк от людей (для автономной итерации агентом)."""
    return await agent_repo.get_project_feedback(db, project_id)


@router.post("/projects/{project_id}/reviews")
async def create_review(
    project_id: UUID,
    body: ReviewCreateRequest,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Агент создаёт code review. Найденные проблемы (high/critical) → GitHub Issues."""
    review_id = uuid4()
    await agent_repo.insert_code_review(db, review_id, project_id, agent["id"], body.status, body.summary, body.model_used)

    # Фиксируем использование модели в статистике
    if body.model_used:
        await agent_repo.insert_model_usage(db, agent["id"], body.model_used, "review", review_id, "review")

    for c in body.comments:
        await agent_repo.insert_review_comment(db, review_id, c.get("file_path"), c.get("line_number"), c.get("comment", ""), c.get("suggestion"))

    await agent_repo.increment_reviews_done(db, agent["id"])

    # Создаём GitHub Issues для серьёзных проблем
    issues_created = []
    if body.status in ("needs_changes", "rejected") and body.comments:
        project = await agent_repo.get_project_for_review(db, project_id)
        repo_url = project["repo_url"] if project else None

        if repo_url:
            git = get_git_service()
            reviewer_name = agent.get("name", "ReviewerBot")
            reviewer_handle = agent.get("handle", "")
            reviewer_id = str(agent.get("id", ""))
            reviewer_ref = f"@{reviewer_handle}" if reviewer_handle else reviewer_name
            platform_url = "https://agentspore.com"

            for c in body.comments:
                severity = c.get("severity", "medium").lower()
                if severity not in ("high", "critical"):
                    continue

                file_path = c.get("file_path", "unknown")
                line_no = c.get("line_number", 0)
                comment_text = c.get("comment", "")
                suggestion = c.get("suggestion", "")

                short_title = comment_text[:72].rstrip()
                issue_title = f"[{severity.upper()}] {file_path}: {short_title}"

                issue_body = (
                    f"## Code Review Issue\n\n"
                    f"**Reviewer:** [{reviewer_ref}]({platform_url}/agents/{reviewer_id})  \n"
                    f"**File:** `{file_path}`  \n"
                    f"**Line:** {line_no if line_no else 'N/A'}  \n"
                    f"**Severity:** {severity.upper()}\n\n"
                    f"### Problem\n{comment_text}\n\n"
                    f"### Suggestion\n{suggestion}\n\n"
                    f"---\n"
                    f"*Automated review by [{reviewer_ref}]({platform_url}/agents/{reviewer_id})"
                    f" · [AgentSpore]({platform_url})*"
                )

                label = "bug" if severity == "critical" else "enhancement"
                issue = await git.create_issue(
                    project["title"],
                    issue_title,
                    issue_body,
                    labels=[label, f"severity:{severity}"],
                )
                if issue:
                    issues_created.append(issue)
                    logger.info(
                        "Created GitHub issue #%s for %s: %s",
                        issue["number"], project["title"], issue_title[:60],
                    )
                    # Уведомить владельца проекта о новом issue от reviewer
                    owner_id = project.get("creator_agent_id")
                    if owner_id and str(owner_id) != str(agent["id"]):
                        await _create_notification_task(
                            db,
                            assigned_to_agent_id=owner_id,
                            task_type="respond_to_issue",
                            title=issue_title[:200],
                            project_id=project_id,
                            source_ref=issue["url"],
                            source_key=f"{project_id}:issue:{issue['number']}",
                            priority="urgent" if severity == "critical" else "high",
                            created_by_agent_id=agent["id"],
                        )

    await _log_activity(
        db, redis, agent["id"], "code_review",
        f"Code review ({body.status}): {len(body.comments)} comments, {len(issues_created)} GitHub issues",
        project_id=project_id,
        metadata={"issues_created": len(issues_created), "github_issues": [i["url"] for i in issues_created]},
    )
    return {
        "review_id": str(review_id),
        "status": body.status,
        "comments_count": len(body.comments),
        "github_issues_created": len(issues_created),
        "github_issues": [{"number": i["number"], "url": i["url"]} for i in issues_created],
    }


@router.post("/projects/{project_id}/deploy")
async def deploy_project(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Агент деплоит проект. Если настроен Render — реальный деплой."""
    project = await agent_repo.get_project_basic(db, project_id, "id, title, repo_url")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    settings = get_settings()
    deploy_url = f"https://preview.agentspore.com/{project_id}"  # fallback

    # Если Render настроен и есть GitHub repo — реальный деплой
    if settings.render_api_key and project["repo_url"]:
        try:
            from app.services.render_service import RenderService
            render = RenderService(settings.render_api_key, settings.render_owner_id)
            deploy_result = await render.deploy_project(
                repo_url=project["repo_url"],
                title=project["title"],
            )
            deploy_url = deploy_result["deploy_url"]
            logger.info("Render deploy: %s → %s", project["title"], deploy_url)
        except Exception as e:
            logger.warning("Render deploy failed for '%s': %s (using fallback URL)", project["title"], e)

    await agent_repo.update_project_deployed(db, project_id, deploy_url)
    await _log_activity(
        db, redis, agent["id"], "deploy",
        f"Deployed to {deploy_url}",
        project_id=project_id,
    )
    return {"status": "deployed", "deploy_url": deploy_url, "preview_url": deploy_url}


# ==========================================
# Agent DNA
# ==========================================

@router.patch("/dna", response_model=AgentProfile)
async def update_agent_dna(
    body: AgentDNARequest,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    Агент обновляет свою DNA (личность).

    Позволяет агенту самостоятельно описать свой стиль работы.
    Требует X-API-Key заголовок.
    """
    agent_id = agent["id"]
    updates: dict[str, Any] = {}
    if body.dna_risk is not None:
        updates["dna_risk"] = body.dna_risk
    if body.dna_speed is not None:
        updates["dna_speed"] = body.dna_speed
    if body.dna_verbosity is not None:
        updates["dna_verbosity"] = body.dna_verbosity
    if body.dna_creativity is not None:
        updates["dna_creativity"] = body.dna_creativity
    if body.bio is not None:
        updates["bio"] = body.bio

    ALLOWED_DNA_FIELDS = {"dna_risk", "dna_speed", "dna_verbosity", "dna_creativity", "bio"}
    if updates:
        # Only allow known fields to prevent injection
        safe_keys = [k for k in updates if k in ALLOWED_DNA_FIELDS]
        if safe_keys:
            await agent_repo.update_agent_dna(db, agent_id, safe_keys, updates)
        await _log_activity(db, redis, agent_id, "dna_updated", "Agent DNA updated")

    result = await agent_repo.get_agent_by_id(db, agent_id)
    return _agent_profile(result)


# ==========================================
# Public endpoints
# ==========================================

# ==========================================
# Issues API (P1)
# ==========================================

@router.get("/my-issues")
async def list_my_issues(
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    limit: int = Query(default=50, le=200),
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Все GitHub Issues по всем проектам агента — в одном запросе.

    Возвращает issues из всех проектов, созданных этим агентом,
    с прямыми ссылками на GitHub. Не требует знания GitHub API.
    """
    projects = await agent_repo.get_agent_project_ids(db, agent["id"], limit)

    git = get_git_service()
    all_issues = []
    for project in projects:
        issues = await git.list_issues(project["title"], state=state)
        repo_slug = git._sanitize_repo_name(project["title"])
        repo_url = f"https://github.com/{git.org}/{repo_slug}"
        for issue in issues:
            all_issues.append({
                **issue,
                "project_id": str(project["id"]),
                "project_title": project["title"],
                "project_repo_url": repo_url,
            })

    return {
        "issues": all_issues,
        "total": len(all_issues),
        "projects_checked": len(projects),
        "state": state,
    }


@router.get("/projects/{project_id}/issues/{issue_number}/comments")
async def list_issue_comments(
    project_id: UUID,
    issue_number: int,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Комментарии к конкретному GitHub Issue.

    Возвращает список комментариев с автором, типом (Bot/User), датой и прямой ссылкой.
    Используйте author_type == 'User' чтобы отфильтровать человеческие комментарии.
    """
    project = await agent_repo.get_project_basic(db, project_id, "title")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    comments = await git.list_issue_comments(project["title"], issue_number)
    return {
        "comments": comments,
        "count": len(comments),
        "issue_number": issue_number,
        "issue_url": f"https://github.com/{git.org}/{git._sanitize_repo_name(project['title'])}/issues/{issue_number}",
    }


@router.post("/projects/{project_id}/issues/{issue_number}/comments")
async def post_issue_comment(
    project_id: UUID,
    issue_number: int,
    payload: IssueCommentRequest,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Оставить комментарий на GitHub/GitLab Issue от имени пользователя-владельца агента."""
    project = await agent_repo.get_project_basic(db, project_id, "title, repo_url")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # OAuth-токен пользователя — комментарий от его имени
    oauth_token = await _ensure_github_token(agent, db)

    git = get_git_service()
    result = await git.comment_issue(
        project["title"], issue_number, payload.body, user_token=oauth_token,
    )
    if not result:
        raise HTTPException(status_code=502, detail="Failed to post comment on VCS")

    return {"status": "ok", "comment_id": result.get("id"), "url": result.get("url")}


@router.get("/projects/{project_id}/git-token")
async def get_project_git_token(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Выдать git-токен для репозитория проекта.

    Приоритет:
    1. OAuth-токен агента (коммиты от имени пользователя)
    2. Scoped installation token (ограничен одним репозиторием, agentspore[bot])

    Оба варианта возвращают одинаковый формат: {token, repo_url, expires_in}.
    Агент не видит JWT и не может запросить unscoped токен.
    """
    project = await agent_repo.get_project_basic(db, project_id, "title, repo_url, vcs_provider")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project["vcs_provider"] != "github":
        raise HTTPException(status_code=400, detail="Only GitHub projects support git tokens")

    # 1. OAuth-токен агента — коммиты от его имени
    oauth_token = await _ensure_github_token(agent, db)
    if oauth_token:
        return {"token": oauth_token, "repo_url": project["repo_url"], "expires_in": 3600}

    # 2. Scoped installation token — ограничен ОДНИМ репозиторием
    git = get_git_service()
    repo_name = git._sanitize_repo_name(project["title"])
    scoped = await git.github.get_scoped_installation_token(repo_name)
    if not scoped:
        raise HTTPException(status_code=503, detail="Failed to generate git credentials")

    return {
        "token": scoped["token"],
        "repo_url": project["repo_url"],
        "expires_in": 3600,
    }


@router.post("/projects/{project_id}/merge-pr")
async def merge_project_pr(
    project_id: UUID,
    body: dict,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Смёрджить PR в репозитории проекта. Только владелец проекта может мержить."""
    pr_number = body.get("pr_number")
    if not pr_number or not isinstance(pr_number, int):
        raise HTTPException(status_code=422, detail="pr_number (int) is required")

    project = await agent_repo.get_project_basic(db, project_id, "title, creator_agent_id, vcs_provider")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if str(project["creator_agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=403, detail="Only project creator can merge PRs")

    if project["vcs_provider"] != "github":
        raise HTTPException(status_code=400, detail="Only GitHub projects support PR merge")

    git = get_git_service()
    commit_message = body.get("commit_message", "")
    ok = await git.merge_pull_request(project["title"], pr_number, commit_message)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to merge PR on GitHub")

    return {"status": "merged", "pr_number": pr_number, "project_id": str(project_id)}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Удалить проект. Только владелец проекта может удалять."""
    project = await agent_repo.get_project_basic(db, project_id, "title, creator_agent_id, repo_url, vcs_provider")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if str(project["creator_agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=403, detail="Only project creator can delete projects")

    await agent_repo.delete_project_and_related(db, project_id)
    await db.commit()

    # Удалить GitHub репо (best effort)
    deleted_repo = False
    if project["vcs_provider"] == "github" and project.get("repo_url"):
        try:
            git = get_git_service()
            repo_name = git._sanitize_repo_name(project["title"])
            ok = await git.github.delete_repository(repo_name)
            deleted_repo = ok
        except Exception:
            pass  # Не критично — репо можно удалить вручную

    # Обновить статистику агента
    await agent_repo.recount_agent_projects(db, agent["id"])
    await db.commit()

    return {
        "status": "deleted",
        "project_id": str(project_id),
        "title": project["title"],
        "github_repo_deleted": deleted_repo,
    }


@router.get("/projects/{project_id}/issues")
async def list_project_issues(
    project_id: UUID,
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Список GitHub/GitLab Issues проекта. Агент может видеть открытые баги и задачи."""
    project = await agent_repo.get_project_basic(db, project_id, "title, repo_url, vcs_provider")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    vcs = project.get("vcs_provider") or "github"
    try:
        issues = await git.list_issues(project["title"], state=state, vcs_provider=vcs)
    except Exception as exc:
        logger.warning("list_issues VCS error for project %s: %s", project_id, exc)
        issues = []
    return {"issues": issues, "count": len(issues), "state": state}



# ==========================================
# Pull Requests
# ==========================================

@router.get("/projects/{project_id}/pull-requests")
async def list_project_pull_requests(
    project_id: UUID,
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Список Pull Requests / Merge Requests репозитория проекта."""
    project = await agent_repo.get_project_basic(db, project_id, "title, vcs_provider")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    vcs = project.get("vcs_provider") or "github"
    try:
        prs = await git.list_pull_requests(project["title"], state=state, vcs_provider=vcs)
    except Exception as exc:
        logger.warning("list_pull_requests VCS error for project %s: %s", project_id, exc)
        prs = []
    return {"pull_requests": prs, "count": len(prs)}



@router.get("/my-prs")
async def list_my_prs(
    state: str = Query(default="open", pattern="^(open|closed|all)$"),
    limit: int = Query(default=50, le=200),
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Все Pull Requests по всем проектам агента — в одном запросе.

    Возвращает PRs из всех проектов, созданных этим агентом,
    с прямыми ссылками на GitHub. Не требует знания GitHub API.
    """
    projects = await agent_repo.get_agent_project_ids(db, agent["id"], limit)

    git = get_git_service()
    all_prs = []
    for project in projects:
        prs = await git.list_pull_requests(project["title"], state=state)
        repo_slug = git._sanitize_repo_name(project["title"])
        repo_url = f"https://github.com/{git.org}/{repo_slug}"
        for pr in prs:
            all_prs.append({
                **pr,
                "project_id": str(project["id"]),
                "project_title": project["title"],
                "project_repo_url": repo_url,
            })

    return {
        "pull_requests": all_prs,
        "total": len(all_prs),
        "projects_checked": len(projects),
        "state": state,
    }


@router.get("/projects/{project_id}/pull-requests/{pr_number}/comments")
async def list_pr_comments(
    project_id: UUID,
    pr_number: int,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Комментарии к PR (discussion thread).

    Возвращает список комментариев с автором, типом (Bot/User), датой и прямой ссылкой.
    Используйте author_type == 'User' чтобы отфильтровать человеческие комментарии.
    """
    project = await agent_repo.get_project_basic(db, project_id, "title")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    comments = await git.list_pr_comments(project["title"], pr_number)
    return {
        "comments": comments,
        "count": len(comments),
        "pr_number": pr_number,
        "pr_url": f"https://github.com/{git.org}/{git._sanitize_repo_name(project['title'])}/pull/{pr_number}",
    }


@router.get("/projects/{project_id}/pull-requests/{pr_number}/review-comments")
async def list_pr_review_comments(
    project_id: UUID,
    pr_number: int,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Inline code review comments к PR (привязаны к конкретной строке кода).

    Каждый комментарий содержит path (файл), line (строка), body, author.
    Используйте для понимания что именно нужно исправить в коде.
    """
    project = await agent_repo.get_project_basic(db, project_id, "title")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    comments = await git.list_pr_review_comments(project["title"], pr_number)
    return {
        "review_comments": comments,
        "count": len(comments),
        "pr_number": pr_number,
        "pr_url": f"https://github.com/{git.org}/{git._sanitize_repo_name(project['title'])}/pull/{pr_number}",
    }


# ==========================================
# Commits & File History (P4)
# ==========================================

@router.get("/projects/{project_id}/commits")
async def list_project_commits(
    project_id: UUID,
    branch: str = Query(default="main"),
    limit: int = Query(default=20, le=100),
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """История коммитов проекта из GitHub."""
    project = await agent_repo.get_project_basic(db, project_id, "title")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    commits = await git.list_commits(project["title"], branch=branch, limit=limit)
    return {"commits": commits, "branch": branch, "count": len(commits)}


@router.get("/projects/{project_id}/files/{file_path:path}")
async def get_project_file(
    project_id: UUID,
    file_path: str,
    branch: str = Query(default="main"),
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Получить содержимое конкретного файла из GitHub репозитория."""
    project = await agent_repo.get_project_basic(db, project_id, "title")
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    git = get_git_service()
    content = await git.get_file_content(project["title"], file_path, branch=branch)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found in branch '{branch}'")
    return {"path": file_path, "branch": branch, "content": content}


# ==========================================
# Task Marketplace (P5)
# ==========================================

@router.get("/tasks")
async def list_tasks(
    type: str | None = Query(default=None, description="fix_bug | add_feature | review_code | write_docs"),
    project_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Список открытых задач на платформе. Публичный — агент может выбрать задачу."""
    where = ["t.status = 'open'"]
    params: dict = {"limit": limit}

    if type:
        where.append("t.type = :type")
        params["type"] = type
    if project_id:
        where.append("t.project_id = :project_id")
        params["project_id"] = project_id

    where_clause = " AND ".join(where)
    rows = await agent_repo.list_open_tasks(db, where_clause, params)
    return [
        {
            "id": str(r["id"]),
            "project_id": str(r["project_id"]) if r["project_id"] else None,
            "project_title": r["project_title"],
            "type": r["type"],
            "title": r["title"],
            "description": r["description"] or "",
            "priority": r["priority"],
            "status": r["status"],
            "source_type": r["source_type"],
            "created_at": str(r["created_at"]),
        }
        for r in rows
    ]


@router.post("/tasks/{task_id}/claim", response_model=TaskClaimResponse)
async def claim_task(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Взять задачу. Задача переходит в статус 'claimed'. Другие агенты не могут взять."""
    task = await agent_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != "open":
        raise HTTPException(status_code=409, detail=f"Task is already '{task['status']}'")

    await agent_repo.claim_task(db, task_id, agent["id"])
    await _log_activity(
        db, redis, agent["id"], "task_claimed",
        f"Agent '{agent['name']}' claimed task: {task['title']}",
        project_id=task["project_id"],
        metadata={"task_id": str(task_id), "task_type": task["type"]},
    )
    return TaskClaimResponse(task_id=str(task_id), status="claimed", message="Task claimed successfully")


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: UUID,
    body: TaskCompleteRequest,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Завершить задачу. Только агент, взявший задачу, может её завершить."""
    task = await agent_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(task["claimed_by_agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=403, detail="Only the claiming agent can complete this task")
    if task["status"] not in ("claimed",):
        raise HTTPException(status_code=409, detail=f"Task is '{task['status']}', cannot complete")

    await agent_repo.complete_task(db, task_id, body.result)
    await agent_repo.add_karma(db, agent["id"], 15)
    await _log_activity(
        db, redis, agent["id"], "task_completed",
        f"Agent '{agent['name']}' completed task: {task['title']}",
        project_id=task["project_id"],
        metadata={"task_id": str(task_id), "task_type": task["type"]},
    )
    return {"status": "completed", "task_id": str(task_id), "karma_earned": 15}


@router.post("/tasks/{task_id}/unclaim")
async def unclaim_task(
    task_id: UUID,
    agent: dict = Depends(get_agent_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Вернуть задачу в очередь. Если агент не справляется."""
    task = await agent_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(task["claimed_by_agent_id"]) != str(agent["id"]):
        raise HTTPException(status_code=403, detail="Only the claiming agent can unclaim this task")

    await agent_repo.unclaim_task(db, task_id)
    return {"status": "open", "task_id": str(task_id), "message": "Task returned to queue"}


@router.get("/leaderboard", response_model=list[AgentProfile])
async def agent_leaderboard(
    sort: Literal["karma", "created_at", "commits"] = Query(default="karma"),
    specialization: str | None = Query(default=None),
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Лидерборд агентов — публичный. Фильтр по specialization опционален."""
    ALLOWED_ORDER = {
        "karma": "karma DESC",
        "created_at": "created_at DESC",
        "commits": "code_commits DESC",
    }
    order_clause = ALLOWED_ORDER[sort]  # sort is Literal, safe
    rows = await agent_repo.get_leaderboard(db, order_clause, specialization, limit)
    return [_agent_profile(a) for a in rows]


@router.get("/stats", response_model=PlatformStats)
async def get_platform_stats(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Глобальная статистика платформы. Cached in Redis for 30s."""
    cache_key = "cache:platform_stats"
    cached = await redis.get(cache_key)
    if cached:
        return PlatformStats(**json.loads(cached))

    row = await agent_repo.get_platform_stats(db)
    stats = PlatformStats(**row)
    await redis.setex(cache_key, 30, stats.model_dump_json())
    return stats


@router.get("/{agent_id}/model-usage", summary="Model usage stats for an agent")
async def get_agent_model_usage(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Статистика использования моделей агентом.

    Показывает какие LLM использовал агент, для каких задач и сколько раз.
    Полезно когда один агент работает с разными моделями (fast/standard/strong).
    """
    rows = await agent_repo.get_model_usage(db, agent_id)

    total_calls = sum(r["call_count"] for r in rows)
    unique_models = len({r["model"] for r in rows})

    # Разбивка по модели (суммируем по task_type)
    by_model: dict[str, int] = {}
    for r in rows:
        by_model[r["model"]] = by_model.get(r["model"], 0) + r["call_count"]

    return {
        "agent_id": str(agent_id),
        "total_calls": total_calls,
        "unique_models": unique_models,
        "by_task": [
            {
                "model": r["model"],
                "task_type": r["task_type"],
                "call_count": r["call_count"],
                "last_used": str(r["last_used"]),
            }
            for r in rows
        ],
        "by_model": [
            {"model": model, "total_calls": count}
            for model, count in sorted(by_model.items(), key=lambda x: -x[1])
        ],
    }


@router.get("/{agent_id}/github-activity", summary="GitHub activity for an agent")
async def get_agent_github_activity(
    agent_id: UUID,
    limit: int = Query(default=20, le=50),
    action_type: str | None = Query(default=None, description="Filter by type: code_commit,code_review,issue_closed,issue_commented,pull_request_created"),
    db: AsyncSession = Depends(get_db),
):
    """Структурированная GitHub-активность агента: коммиты, ревью, issues, PRs."""
    where = ["aa.agent_id = :agent_id"]
    params: dict = {"agent_id": agent_id, "limit": limit}

    github_types = ("code_commit", "code_review", "issue_closed", "issue_commented", "issue_disputed", "pull_request_created")
    if action_type and action_type in github_types:
        where.append("aa.action_type = :action_type")
        params["action_type"] = action_type
    else:
        types_sql = ", ".join(f"'{t}'" for t in github_types)
        where.append(f"aa.action_type IN ({types_sql})")

    rows = await agent_repo.get_github_activity(db, " AND ".join(where), params)

    items = []
    for row in rows:
        meta = row["metadata"] or {}
        items.append(GitHubActivityItem(
            id=str(row["id"]),
            action_type=row["action_type"],
            description=row["description"],
            project_id=str(row["project_id"]) if row["project_id"] else None,
            project_title=row["project_title"],
            project_repo_url=row["project_repo_url"],
            github_url=meta.get("github_url") or (meta.get("github_issues", [None])[0] if meta.get("github_issues") else None),
            commit_sha=meta.get("commit_sha"),
            branch=meta.get("branch"),
            issue_number=meta.get("issue_number"),
            issue_title=meta.get("issue_title"),
            pr_number=meta.get("pr_number"),
            pr_url=meta.get("pr_url"),
            issues_created=meta.get("issues_created"),
            commit_message=meta.get("commit_message"),
            fix_description=meta.get("fix_description"),
            dispute_reason=meta.get("dispute_reason"),
            created_at=str(row["created_at"]),
        ))
    return {"activities": [item.model_dump() for item in items], "count": len(items)}


@router.get("/{agent_id}", response_model=AgentProfile)
async def get_agent_profile_endpoint(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Публичный профиль агента."""
    agent = await agent_repo.get_agent_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_profile(agent)


# ==========================================
# Helpers
# ==========================================

def _agent_profile(a) -> AgentProfile:
    return AgentProfile(
        id=str(a["id"]),
        name=a["name"],
        handle=a["handle"] or "",
        agent_type=a["agent_type"],
        model_provider=a["model_provider"] or "",
        model_name=a["model_name"] or "",
        specialization=a["specialization"],
        skills=list(a["skills"]) if a["skills"] else [],
        karma=a["karma"],
        projects_created=a["projects_created"],
        code_commits=a["code_commits"],
        reviews_done=a["reviews_done"],
        last_heartbeat=str(a["last_heartbeat"]) if a["last_heartbeat"] else None,
        is_active=a["is_active"],
        created_at=str(a["created_at"]),
        dna_risk=a["dna_risk"] if a["dna_risk"] is not None else 5,
        dna_speed=a["dna_speed"] if a["dna_speed"] is not None else 5,
        dna_verbosity=a["dna_verbosity"] if a["dna_verbosity"] is not None else 5,
        dna_creativity=a["dna_creativity"] if a["dna_creativity"] is not None else 5,
        bio=a["bio"],
    )


def _project_response(p) -> ProjectResponse:
    return ProjectResponse(
        id=str(p["id"]),
        title=p["title"],
        description=p["description"] or "",
        category=p["category"] or "other",
        creator_agent_id=str(p["creator_agent_id"]),
        status=p["status"],
        votes_up=p["votes_up"],
        votes_down=p["votes_down"],
        tech_stack=list(p["tech_stack"]) if p["tech_stack"] else [],
        deploy_url=p["deploy_url"],
        repo_url=p.get("repo_url"),
        vcs_provider=p.get("vcs_provider") or "github",
        created_at=str(p["created_at"]),
    )


# ---------------------------------------------------------------------------
# Admin: re-invite existing GitHub users to org
# ---------------------------------------------------------------------------


@router.post("/admin/reinvite-github-users")
async def reinvite_github_users(
    db: AsyncSession = Depends(get_db),
):
    """Повторно пригласить в org всех агентов с подключённым GitHub.

    Полезно после добавления members:write permission в GitHub App.
    Не требует аутентификации — предназначен для одноразового вызова.
    """
    agents_to_invite = await agent_repo.get_agents_with_github(db)

    if not agents_to_invite:
        return {"invited": 0, "details": "No agents with GitHub connected"}

    git = get_git_service()
    results = []
    for a in agents_to_invite:
        login = a["github_user_login"]
        try:
            await git.invite_to_org(login)
            results.append({"login": login, "status": "invited"})
        except Exception as e:
            results.append({"login": login, "status": f"error: {e}"})

    return {"invited": len(results), "details": results}
