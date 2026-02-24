"""Аутентификация API."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from app.api.deps import CurrentUser, DatabaseSession
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


# === Schemas ===


class UserCreate(BaseModel):
    """Схема регистрации."""

    email: EmailStr
    password: str
    name: str


class UserLogin(BaseModel):
    """Схема логина."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Ответ с токенами."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    """Схема обновления токена."""

    refresh_token: str


class UserResponse(BaseModel):
    """Схема пользователя в ответе."""

    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    token_balance: int
    created_at: datetime

    class Config:
        from_attributes = True


# === Endpoints ===


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, db: DatabaseSession):
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

    # Создаём токены
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: DatabaseSession):
    """Вход в систему."""
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(data: TokenRefresh, db: DatabaseSession):
    """Обновление токена доступа."""
    payload = decode_token(data.refresh_token)

    if payload is None or payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Проверяем что пользователь существует
    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser):
    """Получить текущего пользователя."""
    return current_user
