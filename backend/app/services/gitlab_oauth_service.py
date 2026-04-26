"""
GitLab OAuth Service — OAuth авторизация агентов через GitLab.

Поток:
1. Агент регистрируется → генерируется OAuth URL
2. Human (owner) авторизует на GitLab
3. Callback обменивает code на token
4. Агент активируется с GitLab identity
"""

import secrets
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

from loguru import logger

GITLAB_OAUTH_URL = "https://gitlab.com/oauth/authorize"
GITLAB_TOKEN_URL = "https://gitlab.com/oauth/token"
GITLAB_API_URL = "https://gitlab.com/api/v4"

# api — полный доступ к API (включает push, issues, MR)
# read_user — получить login, email, avatar
OAUTH_SCOPES = ["api", "read_user"]


class GitLabOAuthService:
    """Сервис для GitLab OAuth авторизации агентов."""

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.gitlab_oauth_client_id
        self.client_secret = settings.gitlab_oauth_client_secret
        self.redirect_uri = settings.gitlab_oauth_redirect_uri
        self.client = httpx.AsyncClient(timeout=30.0)

    def get_authorization_url(self, agent_id: str) -> dict[str, str]:
        """Генерирует URL для OAuth авторизации."""
        state = f"{agent_id}_{secrets.token_urlsafe(16)}"
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "state": state,
        }
        auth_url = f"{GITLAB_OAUTH_URL}?{urlencode(params)}"
        return {"auth_url": auth_url, "state": state}

    async def exchange_code_for_token(self, code: str) -> dict[str, Any] | None:
        """Обменивает authorization code на access token."""
        try:
            resp = await self.client.post(
                GITLAB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    logger.error("GitLab OAuth error: {}", data.get("error_description", data["error"]))
                    return None
                return data
            logger.error("GitLab token exchange failed: {} {}", resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            logger.error("Error exchanging GitLab code for token: {}", e)
            return None

    async def get_user_info(self, token: str) -> dict[str, Any] | None:
        """Получает информацию о GitLab пользователе."""
        try:
            resp = await self.client.get(
                f"{GITLAB_API_URL}/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error("Failed to get GitLab user info: {}", resp.status_code)
            return None
        except Exception as e:
            logger.error("Error getting GitLab user info: {}", e)
            return None

    async def check_token_validity(self, token: str) -> bool:
        """Проверяет валидность токена."""
        try:
            resp = await self.client.get(
                f"{GITLAB_API_URL}/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def refresh_token(self, refresh_token: str) -> dict[str, Any] | None:
        """Обновляет access token через refresh token (GitLab поддерживает)."""
        try:
            resp = await self.client.post(
                GITLAB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "redirect_uri": self.redirect_uri,
                },
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.error("Error refreshing GitLab token: {}", e)
            return None

    def is_token_expired(self, expires_at: float | None) -> bool:
        """Проверяет истёк ли токен (с буфером 5 минут)."""
        if expires_at is None:
            return False
        return time.time() > (expires_at - 300)

    async def close(self):
        await self.client.aclose()


@lru_cache(maxsize=1)
def get_gitlab_oauth_service() -> GitLabOAuthService:
    return GitLabOAuthService()
