"""Зависимости для API."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import User
from app.services import AIService, TokenService

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> User:
    """Получить текущего пользователя из токена."""
    token = credentials.credentials
    payload = decode_token(token)

    if payload is None or payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_token_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenService:
    """Получить сервис токенов."""
    return TokenService(db)


async def get_ai_service() -> AIService:
    """Получить AI сервис."""
    return AIService()


async def get_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Проверить, что текущий пользователь — администратор."""
    if not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def get_optional_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_optional)],
) -> User | None:
    """Получить текущего пользователя из токена — опционально (None если не авторизован)."""
    if not credentials:
        return None
    payload = decode_token(credentials.credentials)
    if payload is None or payload.type != "access":
        return None
    result = await db.execute(select(User).where(User.id == payload.sub))
    return result.scalar_one_or_none()


# Type aliases для удобства
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_admin_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
TokenSvc = Annotated[TokenService, Depends(get_token_service)]
AISvc = Annotated[AIService, Depends(get_ai_service)]
