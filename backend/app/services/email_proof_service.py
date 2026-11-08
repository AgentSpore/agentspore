"""Track current email ownership separately from legacy login verification."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from httpx import HTTPError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.models.user import User
from app.repositories.email_proof_repo import EmailProofRepository
from app.services.email_service import EmailService


class EmailProofService:
    """Keep proof creation and eligibility on the same current-email contract."""

    SOURCES = frozenset({"email_link", "email_code", "google", "github"})

    @classmethod
    def has_current_proof(cls, user: User) -> bool:
        return bool(
            user.is_verified
            and user.email_proof_email == user.email
            and user.email_proof_at is not None
            and user.email_proof_source in cls.SOURCES
        )

    @classmethod
    def record(cls, user: User, email: str | None, source: str) -> None:
        if email != user.email or source not in cls.SOURCES:
            return
        user.email_proof_email = email
        user.email_proof_at = datetime.now(timezone.utc)
        user.email_proof_source = source

    def __init__(self, repo: EmailProofRepository, redis: Redis, mail: EmailService):
        self.repo, self.redis, self.mail = repo, redis, mail

    async def request(self, user_id: UUID) -> None:
        user = await self.repo.lock_user(user_id)
        if user is None or not user.is_verified:
            raise HTTPException(403, "A verified account is required")
        cfg = get_settings()
        try:
            admitted = await self.redis.set(
                f"email_proof_rate:{user_id}",
                "1",
                nx=True,
                ex=cfg.email_verification_resend_cooldown_seconds,
            )
            if not admitted:
                raise HTTPException(429, "Please wait before requesting another code")
            code = secrets.token_urlsafe(32)
            digest = hashlib.sha256(code.encode()).hexdigest()
            user.verification_token = digest
            user.verification_email = user.email
            user.verification_purpose = "email_proof"
            user.verification_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=cfg.email_verification_ttl_seconds
            )
            await self.repo.flush()
            await self.redis.setex(
                f"email_proof:{user_id}:{digest}", cfg.email_verification_ttl_seconds, "1"
            )
        except RedisError:
            raise HTTPException(503, "Email verification is temporarily unavailable") from None
        try:
            sent = await self.mail.send_email_proof_code(user.email, code)
        except HTTPError:
            raise HTTPException(503, "Email verification is temporarily unavailable") from None
        if not sent:
            raise HTTPException(503, "Email verification is temporarily unavailable")

    async def confirm(self, user_id: UUID, code: str) -> None:
        user = await self.repo.lock_user(user_id)
        digest = hashlib.sha256(code.encode()).hexdigest()
        if (
            user is None
            or not user.is_verified
            or user.verification_purpose != "email_proof"
            or user.verification_email != user.email
            or not user.verification_token
            or not secrets.compare_digest(user.verification_token, digest)
            or user.verification_expires_at is None
            or user.verification_expires_at <= datetime.now(timezone.utc)
        ):
            raise HTTPException(400, "Invalid or expired email proof code")
        try:
            consumed = await self.redis.getdel(f"email_proof:{user_id}:{digest}")
        except RedisError:
            raise HTTPException(503, "Email verification is temporarily unavailable") from None
        if not consumed:
            raise HTTPException(400, "Invalid or expired email proof code")
        self.record(user, user.email, "email_code")
        user.verification_token = None
        user.verification_email = None
        user.verification_purpose = None
        user.verification_expires_at = None
        await self.repo.flush()
