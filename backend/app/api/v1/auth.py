"""Аутентификация API."""

import hashlib
import secrets
import uuid
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.core.redis_client import get_redis
from app.services.agent_service import AgentService, get_agent_service
from app.services.email_service import EmailService, get_email_service
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.models import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)

from loguru import logger

router = APIRouter(prefix="/auth", tags=["auth"])


# === Endpoints ===


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    db: DatabaseSession,
    agent_svc: AgentService = Depends(get_agent_service),
):
    """Регистрация нового пользователя."""
    # Case-insensitive duplicate check. data.email is already lowercased by the
    # pydantic validator, so we only need to lowercase the column side.
    # Functional index idx_users_email_lower (V48) keeps this O(log n).
    result = await db.execute(select(User).where(func.lower(User.email) == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Создаём пользователя
    user = User(
        email=data.email,
        hashed_password=get_password_hash(data.password),
        name=data.name,
        token_balance=0,
    )
    db.add(user)
    await db.flush()

    # Автопривязка агентов по owner_email
    await agent_svc.link_agents_by_email(user.id, data.email)

    # Создаём токены
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin,
    db: DatabaseSession,
    agent_svc: AgentService = Depends(get_agent_service),
):
    """Вход в систему."""
    result = await db.execute(select(User).where(func.lower(User.email) == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Автопривязка агентов по owner_email (при логине тоже)
    await agent_svc.link_agents_by_email(user.id, data.email)

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    data: TokenRefresh,
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Обновление токена доступа. Old refresh token is blacklisted."""
    payload = decode_token(data.refresh_token)

    if payload is None or payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Check if token is blacklisted
    token_hash = hashlib.sha256(data.refresh_token.encode()).hexdigest()
    if await redis.exists(f"blacklist:refresh:{token_hash}"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )

    # Проверяем что пользователь существует
    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Blacklist the old refresh token (TTL = remaining token lifetime)
    remaining_seconds = max(int((payload.exp - datetime.now(payload.exp.tzinfo)).total_seconds()), 1)
    await redis.setex(f"blacklist:refresh:{token_hash}", remaining_seconds, "1")

    access_token = create_access_token(str(user.id))
    new_refresh = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser):
    """Получить текущего пользователя."""
    return current_user


# === Password Reset ===


@router.post("/forgot-password")
async def forgot_password(
    data: ForgotPasswordRequest,
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
    email_svc: EmailService = Depends(get_email_service),
):
    """Запрос сброса пароля. Всегда возвращает 200 (без утечки информации о существовании email)."""
    cfg = get_settings()
    generic = {"message": "If the email exists, a reset link has been sent."}

    result = await db.execute(select(User).where(func.lower(User.email) == data.email))
    user = result.scalar_one_or_none()

    # OAuth-only users or non-existent — silent return
    if not user or not user.hashed_password:
        return generic

    # Rate limit
    rate_key = f"password_reset_rate:{data.email}"
    current = await redis.incr(rate_key)
    if current == 1:
        await redis.expire(rate_key, cfg.password_reset_ttl_seconds)
    if current > cfg.password_reset_rate_limit:
        return generic

    # Generate and store token
    token = secrets.token_urlsafe(32)
    await redis.setex(f"password_reset:{token}", cfg.password_reset_ttl_seconds, str(user.id))

    # Send email
    sent = await email_svc.send_password_reset(data.email, token)
    if sent:
        logger.info("Password reset email sent to {}", data.email)

    return generic


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Сброс пароля по токену из email."""
    redis_key = f"password_reset:{data.token}"
    user_id = await redis.get(redis_key)

    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user_id = user_id.decode() if isinstance(user_id, bytes) else user_id
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()

    # One-time use — delete token
    await redis.delete(redis_key)

    logger.info("Password reset completed for user {}", user.email)
    return {"message": "Password has been reset successfully."}
