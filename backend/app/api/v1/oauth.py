"""OAuth авторизация для пользователей (Google, GitHub)."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DatabaseSession
from app.core.config import get_settings
from app.core.security import create_access_token, create_refresh_token
from app.models import User

router = APIRouter(prefix="/oauth", tags=["oauth"])
settings = get_settings()

FRONTEND_URL = settings.oauth_redirect_base_url.replace(":8000", ":3000")


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
    # Попытка найти по oauth_id
    result = await db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_id == oauth_id)
    )
    user = result.scalar_one_or_none()
    if user:
        return user

    # Проверить нет ли уже аккаунта с таким email (и прилинковать)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        user.oauth_provider = provider
        user.oauth_id = oauth_id
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        await db.flush()
        return user

    # Создать нового пользователя
    user = User(
        email=email,
        name=name,
        avatar_url=avatar_url,
        oauth_provider=provider,
        oauth_id=oauth_id,
        token_balance=50,
    )
    db.add(user)
    await db.flush()
    return user


# ── Google ──────────────────────────────────────────────────────────────────

@router.get("/google")
async def google_login():
    """Redirect → Google OAuth consent screen."""
    if not settings.google_oauth_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured")
    params = (
        "response_type=code"
        f"&client_id={settings.google_oauth_client_id}"
        f"&redirect_uri={settings.oauth_redirect_base_url}/api/v1/oauth/google/callback"
        "&scope=openid+email+profile"
        "&access_type=offline"
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=302)


@router.get("/google/callback")
async def google_callback(code: str, db: DatabaseSession):
    """Обработать callback от Google и выдать JWT."""
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
async def github_login():
    """Redirect → GitHub OAuth consent screen."""
    if not settings.user_github_oauth_client_id:
        raise HTTPException(status_code=501, detail="GitHub user OAuth not configured")
    params = (
        f"client_id={settings.user_github_oauth_client_id}"
        f"&redirect_uri={settings.oauth_redirect_base_url}/api/v1/oauth/github/callback"
        "&scope=user:email"
    )
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}", status_code=302)


@router.get("/github/callback")
async def github_callback(code: str, db: DatabaseSession):
    """Обработать callback от GitHub и выдать JWT."""
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
