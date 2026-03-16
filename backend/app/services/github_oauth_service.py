"""
GitHub OAuth Service — OAuth авторизация агентов через GitHub.

Каждый ИИ-агент получает свой GitHub identity через OAuth авторизацию.
Это позволяет агентам:
- Создавать репозитории от своего имени
- Делать коммиты со своим авторством
- Работать с GitHub API от имени пользователя

Поток:
1. Агент регистрируется → генерируется OAuth URL
2. Human (owner) авторизует на GitHub
3. Callback обменивает code на token
4. Агент активируется с GitHub identity
"""

import secrets
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

from loguru import logger

GITHUB_OAUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"

# OAuth scopes needed for agents
# repo     - full repo access: create repos in org, push code, comment on issues/PRs
# read:user - identify the user (login, email, avatar)
OAUTH_SCOPES = ["repo", "read:user"]


class GitHubOAuthService:
    """Сервис для GitHub OAuth авторизации агентов."""

    def __init__(self):
        settings = get_settings()
        self.client_id = settings.github_oauth_client_id
        self.client_secret = settings.github_oauth_client_secret
        self.redirect_uri = settings.github_oauth_redirect_uri
        self.client = httpx.AsyncClient(timeout=30.0)

    def get_authorization_url(self, agent_id: str) -> dict[str, str]:
        """
        Генерирует URL для OAuth авторизации.

        Args:
            agent_id: UUID агента для связи state с агентом

        Returns:
            {
                "auth_url": "https://github.com/login/oauth/authorize?...",
                "state": "random_state_string"
            }
        """
        # Генерируем случайный state для CSRF защиты
        state = f"{agent_id}_{secrets.token_urlsafe(16)}"

        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(OAUTH_SCOPES),
            "state": state,
            "allow_signup": "true",  # Allow new users
        }

        auth_url = f"{GITHUB_OAUTH_URL}?{urlencode(params)}"

        return {
            "auth_url": auth_url,
            "state": state,
        }

    async def exchange_code_for_token(self, code: str) -> dict[str, Any] | None:
        """
        Обменивает authorization code на access token.

        Args:
            code: Authorization code from GitHub callback

        Returns:
            {
                "access_token": "...",
                "token_type": "bearer",
                "scope": "repo,read:org",
                "refresh_token": "...",  # if available
                "expires_in": 28800,     # if available
            }
            or None on error
        """
        try:
            resp = await self.client.post(
                GITHUB_TOKEN_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    logger.error(f"GitHub OAuth error: {data.get('error_description', data['error'])}")
                    return None
                return data
            else:
                logger.error(f"GitHub token exchange failed: {resp.status_code} {resp.text[:200]}")
                return None

        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}")
            return None

    async def get_user_info(self, token: str) -> dict[str, Any] | None:
        """
        Получает информацию о GitHub пользователе.

        Args:
            token: OAuth access token

        Returns:
            {
                "id": 12345678,
                "login": "username",
                "name": "Full Name",
                "email": "user@example.com",
                "avatar_url": "https://...",
                "html_url": "https://github.com/username",
            }
            or None on error
        """
        try:
            resp = await self.client.get(
                f"{GITHUB_API_URL}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"Failed to get user info: {resp.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            return None

    async def check_token_validity(self, token: str) -> bool:
        """
        Проверяет валидность токена.

        Returns:
            True if token is valid, False otherwise
        """
        try:
            resp = await self.client.get(
                f"{GITHUB_API_URL}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def revoke_token(self, token: str) -> bool:
        """
        Отзывает OAuth токен.

        Returns:
            True if revoked successfully, False otherwise
        """
        try:
            # GitHub требует Basic Auth с client_id:client_secret для revocation
            resp = await self.client.delete(
                f"https://api.github.com/applications/{self.client_id}/grant",
                headers={
                    "Accept": "application/vnd.github+json",
                },
                auth=(self.client_id, self.client_secret),
                json={"access_token": token},
            )

            # 204 No Content = success
            return resp.status_code == 204

        except Exception as e:
            logger.error(f"Error revoking token: {e}")
            return False

    def is_token_expired(self, expires_at: float | None) -> bool:
        """
        Проверяет, истёк ли токен.

        Args:
            expires_at: Unix timestamp или None

        Returns:
            True if expired or no expiration info, False if still valid
        """
        if expires_at is None:
            return False  # No expiration info = assume valid

        # Add 5 minute buffer
        return time.time() > (expires_at - 300)

    async def refresh_token(self, refresh_token: str) -> dict[str, Any] | None:
        """
        Обновляет OAuth access token через refresh_token.

        GitHub Apps с user-to-server tokens поддерживают refresh.
        Classic OAuth Apps не выдают refresh_token (токены не протухают).

        Returns:
            New token data dict or None on failure.
        """
        try:
            resp = await self.client.post(
                GITHUB_TOKEN_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    logger.error("GitHub refresh error: %s", data.get("error_description", data["error"]))
                    return None
                return data
            else:
                logger.error("GitHub token refresh failed: %s", resp.status_code)
                return None
        except Exception as e:
            logger.error("Error refreshing GitHub token: %s", e)
            return None

    async def ensure_valid_token(
        self, token: str, refresh_token: str | None, expires_at: Any | None,
    ) -> dict[str, str | None] | None:
        """
        Проверяет валидность токена и обновляет при необходимости.

        Returns:
            {"access_token": str, "refresh_token": str|None, "expires_at": datetime|None}
            если токен обновлён, иначе None (токен валиден, обновление не нужно).
        """
        from datetime import datetime, timedelta

        # Конвертируем expires_at в timestamp если это datetime
        ts = None
        if expires_at is not None:
            if isinstance(expires_at, datetime):
                ts = expires_at.timestamp()
            elif isinstance(expires_at, (int, float)):
                ts = float(expires_at)

        # Если токен не истёк — проверяем простым вызовом API
        if not self.is_token_expired(ts):
            return None  # Всё OK, обновление не нужно

        logger.info("GitHub OAuth token expired, attempting refresh")

        # Пробуем refresh если есть refresh_token
        if refresh_token:
            new_data = await self.refresh_token(refresh_token)
            if new_data and "access_token" in new_data:
                new_expires_in = new_data.get("expires_in")
                new_expires_at = (
                    datetime.utcnow() + timedelta(seconds=new_expires_in)
                    if new_expires_in
                    else None
                )
                logger.info("GitHub OAuth token refreshed successfully")
                return {
                    "access_token": new_data["access_token"],
                    "refresh_token": new_data.get("refresh_token", refresh_token),
                    "expires_at": new_expires_at,
                }

        # Refresh не удался — проверяем валидность текущего токена через API
        if await self.check_token_validity(token):
            logger.info("GitHub OAuth token still valid despite expiry timestamp")
            return None

        logger.warning("GitHub OAuth token expired and refresh failed — token is invalid")
        return {"access_token": None, "refresh_token": None, "expires_at": None}

    async def close(self):
        """Закрыть HTTP клиент."""
        await self.client.aclose()


@lru_cache(maxsize=1)
def get_github_oauth_service() -> GitHubOAuthService:
    """Получить singleton экземпляр GitHubOAuthService."""
    return GitHubOAuthService()
