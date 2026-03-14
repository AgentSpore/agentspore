"""Аутентификация API."""

import hashlib
import uuid
from datetime import datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession
from app.core.redis_client import get_redis
from app.services.agent_service import AgentService, get_agent_service
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.models import User
from app.schemas.auth import TokenRefresh, TokenResponse, UserCreate, UserLogin, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


# === Endpoints ===


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    data: UserCreate,
    db: DatabaseSession,
    agent_svc: AgentService = Depends(get_agent_service),
):
    """Регистрация нового пользователя."""
    # Проверяем существование email
    result = await db.execute(select(User).where(User.email == data.email))
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
        token_balance=50,  # Бонус за регистрацию
    )
    db.add(user)
    await db.flush()

    # Автопривязка агентов по owner_email
    await agent_svc.link_agents_by_email(db, user.id, data.email)

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
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Автопривязка агентов по owner_email (при логине тоже)
    await agent_svc.link_agents_by_email(db, user.id, data.email)

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
