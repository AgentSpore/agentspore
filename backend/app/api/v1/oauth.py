"""OAuth авторизация для пользователей (Google, GitHub)."""

import secrets

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.api.deps import DatabaseSession
from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.core.security import create_access_token, create_refresh_token
from app.models import User

router = APIRouter(prefix="/oauth", tags=["oauth"])
settings = get_settings()

FRONTEND_URL = settings.oauth_redirect_base_url.replace(":8000", ":3000")

_OAUTH_STATE_TTL = 300  # 5 minutes


async def _store_oauth_state(redis: aioredis.Redis, state: str) -> None:
    """Store CSRF state token in Redis with 5-minute TTL."""
    await redis.setex(f"oauth_state:{state}", _OAUTH_STATE_TTL, "1")


async def _consume_oauth_state(redis: aioredis.Redis, state: str) -> bool:
    """Validate and delete (single-use) CSRF state token. Returns False if invalid."""
    key = f"oauth_state:{state}"
    deleted = await redis.delete(key)
    return deleted == 1


def _make_token_response_redirect(user_id: str) -> RedirectResponse:
    access = create_access_token(user_id)
    refresh = create_refresh_token(user_id)
    return RedirectResponse(
        f"{FRONTEND_URL}/auth/callback?access_token={access}&refresh_token={refresh}",
        status_code=302,
    )


async def _upsert_oauth_user(
    db: DatabaseSession,
    *,
    provider: str,
    oauth_id: str,
    email: str,
    name: str,
    avatar_url: str | None,
) -> User:
    """Найти или создать пользователя через OAuth."""
    # Normalize OAuth-provided email to lowercase — matches schema validator
    # convention so GitHub/Google accounts link to existing email-password ones.
    email = (email or "").strip().lower()

    # Попытка найти по oauth_id
    result = await db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_id == oauth_id)
    )
    user = result.scalar_one_or_none()
    if user:
        return user

    # Проверить нет ли уже аккаунта с таким email (и прилинковать)
    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalar_one_or_none()
    if user:
        user.oauth_provider = provider
        user.oauth_id = oauth_id
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        # Linking OAuth to an existing email-password account verifies the email
        if not user.is_verified:
            user.is_verified = True
            user.verification_token = None
            user.verification_expires_at = None
        await db.flush()
        return user

    # Создать нового пользователя — OAuth accounts are pre-verified (provider vouches for email)
    user = User(
        email=email,
        name=name,
        avatar_url=avatar_url,
        oauth_provider=provider,
        oauth_id=oauth_id,
        token_balance=50,
        is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


# ── Google ──────────────────────────────────────────────────────────────────

@router.get("/google")
async def google_login(redis: aioredis.Redis = Depends(get_redis)):
    """Redirect → Google OAuth consent screen (CSRF-protected via state param)."""
    if not settings.google_oauth_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    state = secrets.token_urlsafe(32)
    await _store_oauth_state(redis, state)
    params = (
        "response_type=code"
        f"&client_id={settings.google_oauth_client_id}"
        f"&redirect_uri={settings.oauth_redirect_base_url}/api/v1/oauth/google/callback"
        "&scope=openid+email+profile"
        "&access_type=offline"
        f"&state={state}"
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=302)


@router.get("/google/callback")
async def google_callback(
    code: str,
    db: DatabaseSession,
    state: str = Query(...),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Обработать callback от Google и выдать JWT."""
    if not await _consume_oauth_state(redis, state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if not settings.google_oauth_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")

    async with httpx.AsyncClient() as client:
        # Обменять code → tokens
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": f"{settings.oauth_redirect_base_url}/api/v1/oauth/google/callback",
                "grant_type": "authorization_code",
            },
        )
        if token_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Google token exchange failed")
        tokens = token_res.json()

        # Получить профиль
        profile_res = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if profile_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Google profile")
        profile = profile_res.json()

    user = await _upsert_oauth_user(
        db,
        provider="google",
        oauth_id=profile["sub"],
        email=profile["email"],
        name=profile.get("name", profile["email"].split("@")[0]),
        avatar_url=profile.get("picture"),
    )
    await db.commit()
    return _make_token_response_redirect(str(user.id))


# ── GitHub ───────────────────────────────────────────────────────────────────

@router.get("/github")
async def github_login(redis: aioredis.Redis = Depends(get_redis)):
    """Redirect → GitHub OAuth consent screen (CSRF-protected via state param)."""
    if not settings.user_github_oauth_client_id:
        raise HTTPException(status_code=501, detail="GitHub user OAuth not configured")
    state = secrets.token_urlsafe(32)
    await _store_oauth_state(redis, state)
    params = (
        f"client_id={settings.user_github_oauth_client_id}"
        f"&redirect_uri={settings.oauth_redirect_base_url}/api/v1/oauth/github/callback"
        "&scope=user:email"
        f"&state={state}"
    )
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}", status_code=302)


@router.get("/github/callback")
async def github_callback(
    code: str,
    db: DatabaseSession,
    state: str = Query(...),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Обработать callback от GitHub и выдать JWT."""
    if not await _consume_oauth_state(redis, state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if not settings.user_github_oauth_client_id:
        raise HTTPException(status_code=501, detail="GitHub user OAuth not configured")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.user_github_oauth_client_id,
                "client_secret": settings.user_github_oauth_client_secret,
                "code": code,
                "redirect_uri": f"{settings.oauth_redirect_base_url}/api/v1/oauth/github/callback",
            },
            headers={"Accept": "application/json"},
        )
        if token_res.status_code != 200:
            raise HTTPException(status_code=400, detail="GitHub token exchange failed")
        gh_token = token_res.json().get("access_token")
        if not gh_token:
            raise HTTPException(status_code=400, detail="GitHub token exchange failed")

        # Получить профиль
        profile_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"},
        )
        profile = profile_res.json()

        # Email может быть скрыт — дополнительный запрос
        email = profile.get("email")
        if not email:
            emails_res = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"},
            )
            if emails_res.status_code == 200:
                for e in emails_res.json():
                    if e.get("primary") and e.get("verified"):
                        email = e["email"]
                        break

    if not email:
        raise HTTPException(status_code=400, detail="Could not retrieve email from GitHub")

    user = await _upsert_oauth_user(
        db,
        provider="github",
        oauth_id=str(profile["id"]),
        email=email,
        name=profile.get("name") or profile.get("login", email.split("@")[0]),
        avatar_url=profile.get("avatar_url"),
    )
    await db.commit()
    return _make_token_response_redirect(str(user.id))
