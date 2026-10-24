"""Зависимости для API."""

import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import decode_token
from app.models import User
from app.schemas.usage_observer import UsageObservation, UsageObserverTarget
from app.services.usage_observer_service import UsageObserverService
from app.services.usage_service import UsageService

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    """Extract real client IP with trusted-proxy validation.

    Only honours X-Forwarded-For when the direct connection originates from a
    configured trusted proxy (e.g. Caddy container).  Prevents header spoofing
    by untrusted callers.

    Configuration: set TRUSTED_PROXY_IPS env var (comma-separated CIDRs/IPs).
    Default: 127.0.0.1 (local Caddy).  Prod should set Caddy container IP.
    """
    settings = get_settings()
    raw_host = request.client.host if request.client else None

    if raw_host is None:
        return "unknown"

    trusted: list[str] = settings.trusted_proxy_ips
    is_trusted = False
    try:
        client_addr = ipaddress.ip_address(raw_host)
        for entry in trusted:
            try:
                if "/" in entry:
                    if client_addr in ipaddress.ip_network(entry, strict=False):
                        is_trusted = True
                        break
                else:
                    if client_addr == ipaddress.ip_address(entry):
                        is_trusted = True
                        break
            except ValueError:
                continue
    except ValueError:
        pass

    if is_trusted:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # Rightmost-trusted strategy: use the leftmost IP from XFF header
            # (the first untrusted hop as seen by the proxy).
            return forwarded.split(",")[0].strip()

    return raw_host


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


def get_usage_service(db: DatabaseSession) -> UsageService:
    """Inject the request-owned database transaction into service usage policy."""
    return UsageService(db)


async def get_usage_observer_metadata(request: Request) -> UsageObservation:
    """Authenticate and validate producer transport before any observer database dependency."""
    return await UsageObserverService.metadata(request, get_settings())


async def get_usage_observer_service(
    # INVARIANT(U171.7): producer validation must precede database dependencies.
    _observation: Annotated[UsageObservation, Depends(get_usage_observer_metadata)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_optional)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> UsageObserverService:
    """Adapt optional-user lookup to the same function-scoped, pre-response transaction."""
    user = await get_optional_user(db, credentials)
    settings = get_settings()
    if settings.otkrytka_usage_project_id is None or settings.otkrytka_usage_observer_key is None:
        raise HTTPException(503, "Usage observer unavailable")
    target = UsageObserverTarget(project_id=settings.otkrytka_usage_project_id,
                                 key=settings.otkrytka_usage_observer_key)
    return UsageObserverService(UsageService(db), redis, target, user.id if user else None)
