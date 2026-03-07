"""Зависимости для API."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_token
from app.models import User

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


async def get_optional_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_optional)],
) -> User | None:
    """Получить текущего пользователя если токен предоставлен."""
    if credentials is None:
        return None
    token = credentials.credentials
    payload = decode_token(token)
    if payload is None or payload.type != "access":
        return None
    result = await db.execute(select(User).where(User.id == payload.sub))
    return result.scalar_one_or_none()


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


async def get_admin_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Проверить что пользователь — администратор."""
    if not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# Type aliases
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
