"""Аутентификация API."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.core.redis_client import get_redis
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
    RegisterResponse,
    ResetPasswordRequest,
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from app.services.agent_service import AgentService, get_agent_service
from app.services.email_service import EmailService, get_email_service

from loguru import logger

router = APIRouter(prefix="/auth", tags=["auth"])


# === Rate limit helpers ===

def _client_ip(request: Request) -> str:
    """Extract real client IP, honouring X-Forwarded-For set by Caddy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _check_ip_rate_limit(
    redis: aioredis.Redis,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Increment counter and raise 429 if limit exceeded within the window.

    Uses a simple INCR + EXPIRE pattern (atomic on first hit).
    """
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )


# === Endpoints ===


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_202_ACCEPTED)
async def register(
    data: UserCreate,
    request: Request,
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
    agent_svc: AgentService = Depends(get_agent_service),
    email_svc: EmailService = Depends(get_email_service),
):
    """Регистрация нового пользователя.

    Enforces:
    - 3 registrations per IP per hour (Redis-backed).
    - Single-use email verification token sent via Resend; login blocked until verified.
    """
    cfg = get_settings()

    # Per-IP rate limit: 3 registrations / hour
    ip = _client_ip(request)
    await _check_ip_rate_limit(
        redis,
        f"register_rate:{ip}",
        cfg.register_rate_limit,
        cfg.register_rate_window_seconds,
    )

    # Case-insensitive duplicate check. data.email is already lowercased by the
    # pydantic validator; func.lower() on the column side uses idx_users_email_lower (V48).
    result = await db.execute(select(User).where(func.lower(User.email) == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Generate verification token (32 bytes → 43-char urlsafe string).
    # Store only the SHA-256 hash in the DB so a DB dump cannot be replayed.
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=cfg.email_verification_ttl_seconds)

    # Create user (unverified)
    user = User(
        email=data.email,
        hashed_password=get_password_hash(data.password),
        name=data.name,
        token_balance=0,
        is_verified=False,
        verification_token=token_hash,
        verification_expires_at=expires_at,
    )
    db.add(user)
    await db.flush()

    # Auto-link agents by owner_email
    await agent_svc.link_agents_by_email(user.id, data.email)

    # Persist token in Redis (authoritative TTL)
    await redis.setex(
        f"email_verify:{raw_token}",
        cfg.email_verification_ttl_seconds,
        str(user.id),
    )

    # Send verification email (best-effort; dev falls back to console log)
    sent = await email_svc.send_verification_email(data.email, raw_token)
    if not sent:
        verify_url = f"{cfg.oauth_redirect_base_url}/api/v1/auth/verify-email?token={raw_token}"
        logger.warning("Email not sent — dev verify link: {}", verify_url)

    return RegisterResponse()


@router.get("/verify-email")
async def verify_email(
    token: str,
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
):
    """Подтвердить email по одноразовому токену из письма.

    On success: marks user as verified and returns JWT pair so the frontend
    can log the user in immediately without a second round-trip.
    """
    redis_key = f"email_verify:{token}"
    user_id = await redis.get(redis_key)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link.",
        )

    user_id = user_id if isinstance(user_id, str) else user_id.decode()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link.",
        )

    if user.is_verified:
        # Idempotent — already verified; just return fresh tokens
        await redis.delete(redis_key)
        return {
            "message": "Email already verified.",
            **TokenResponse(
                access_token=create_access_token(str(user.id)),
                refresh_token=create_refresh_token(str(user.id)),
            ).model_dump(),
        }

    # Mark verified and clear token fields
    user.is_verified = True
    user.verification_token = None
    user.verification_expires_at = None
    await db.commit()

    # One-time use — delete from Redis
    await redis.delete(redis_key)

    logger.info("Email verified for user {}", user.email)

    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    return {
        "message": "Email verified successfully.",
        **TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
        ).model_dump(),
    }


@router.post("/resend-verification")
async def resend_verification(
    data: ForgotPasswordRequest,  # reuses email-only schema
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
    email_svc: EmailService = Depends(get_email_service),
):
    """Повторно отправить письмо верификации.

    Rate-limited to 1 request per email per minute.
    Always returns 200 to avoid leaking whether the account exists.
    """
    cfg = get_settings()
    generic = {"message": "If the account exists and is unverified, a new link has been sent."}

    result = await db.execute(select(User).where(func.lower(User.email) == data.email))
    user = result.scalar_one_or_none()

    if not user or user.is_verified:
        return generic

    # Rate limit: 1/min per email
    rate_key = f"verify_resend_rate:{data.email}"
    current = await redis.incr(rate_key)
    if current == 1:
        await redis.expire(rate_key, cfg.email_verification_resend_cooldown_seconds)
    if current > 1:
        return generic  # silent — don't reveal the cooldown to scrapers

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=cfg.email_verification_ttl_seconds)

    user.verification_token = token_hash
    user.verification_expires_at = expires_at
    await db.commit()

    await redis.setex(
        f"email_verify:{raw_token}",
        cfg.email_verification_ttl_seconds,
        str(user.id),
    )

    await email_svc.send_verification_email(data.email, raw_token)
    return generic


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin,
    request: Request,
    db: DatabaseSession,
    redis: aioredis.Redis = Depends(get_redis),
    agent_svc: AgentService = Depends(get_agent_service),
):
    """Вход в систему.

    Enforces:
    - 5 failed attempts per IP per 15 minutes → 429 (counter resets on success).
    - Blocked if account is not email-verified (OAuth users are pre-verified).
    """
    cfg = get_settings()
    ip = _client_ip(request)
    fail_key = f"login_fail:{ip}"

    # Check brute-force counter before DB hit
    fail_count_raw = await redis.get(fail_key)
    fail_count = int(fail_count_raw) if fail_count_raw else 0
    if fail_count >= cfg.login_fail_rate_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again in 15 minutes.",
        )

    result = await db.execute(select(User).where(func.lower(User.email) == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        # Increment fail counter
        new_count = await redis.incr(fail_key)
        if new_count == 1:
            await redis.expire(fail_key, cfg.login_fail_rate_window_seconds)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Block unverified accounts (OAuth users have is_verified=True from V61 backfill)
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    # Success — clear fail counter
    await redis.delete(fail_key)

    # Auto-link agents by owner_email
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

    result = await db.execute(select(User).where(User.id == payload.sub))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Blacklist the old refresh token (TTL = remaining lifetime)
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

    # Rate limit: cfg.password_reset_rate_limit per TTL window per email
    rate_key = f"password_reset_rate:{data.email}"
    current = await redis.incr(rate_key)
    if current == 1:
        await redis.expire(rate_key, cfg.password_reset_ttl_seconds)
    if current > cfg.password_reset_rate_limit:
        return generic

    # Generate and store token (cryptographically random, single-use via Redis delete)
    token = secrets.token_urlsafe(32)
    await redis.setex(f"password_reset:{token}", cfg.password_reset_ttl_seconds, str(user.id))

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

    user_id = user_id if isinstance(user_id, str) else user_id.decode()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.hashed_password = get_password_hash(data.new_password)
    await db.commit()

    # One-time use — delete token immediately after consumption
    await redis.delete(redis_key)

    logger.info("Password reset completed for user {}", user.email)
    return {"message": "Password has been reset successfully."}
