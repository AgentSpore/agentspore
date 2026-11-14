"""Change the account email through a mailbox-proven, single-use confirmation."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from httpx import HTTPError
from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.security import verify_password
from app.models.user import User
from app.repositories.email_proof_repo import EmailProofRepository
from app.services.email_proof_service import EmailProofService
from app.services.email_service import EmailService
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService

PURPOSE = "email_change"
UNAVAILABLE = "Email change is temporarily unavailable"
GENERIC = "If the address can be used, a confirmation link was sent to it."


class EmailChangeService:
    """The old address stays active until the new one proves ownership."""

    def __init__(self, repo: EmailProofRepository, redis: Redis, mail: EmailService):
        self.repo, self.redis, self.mail = repo, redis, mail

    async def request(self, user_id: UUID, new_email: str, password: str | None) -> str:
        """Start a change; usable, taken and unchanged addresses get the same reply."""
        user = await self.repo.lock_user(user_id)
        if user is None or not user.is_verified:
            raise HTTPException(403, "A verified account is required")
        await self._admit(user_id)
        self._reauthenticate(user, password)
        if WelcomeBeneficiaryService.is_disposable_domain(new_email.rpartition("@")[2]):
            raise HTTPException(422, "Disposable email addresses are not accepted")
        if new_email == user.email.lower() or await self.repo.email_taken(new_email, user_id):
            return GENERIC
        await self._issue(user, new_email)
        return GENERIC

    async def confirm(self, user_id: UUID, token: str) -> None:
        """Switch the email, prove the new mailbox, and warn the previous address."""
        user = await self.repo.lock_user(user_id)
        digest = hashlib.sha256(token.encode()).hexdigest()
        new_email = user.verification_email if user else None
        if (
            user is None
            or not user.is_verified
            or user.verification_purpose != PURPOSE
            or not new_email
            or not user.verification_token
            or not secrets.compare_digest(user.verification_token, digest)
            or user.verification_expires_at is None
            or user.verification_expires_at <= datetime.now(timezone.utc)
            or not await self._consume(user_id, digest)
            or await self.repo.email_taken(new_email, user_id)
        ):
            raise HTTPException(400, "Invalid or expired email change link")
        old_email = user.email
        user.email = new_email
        self._clear_pending(user)
        user.email_proof_email = user.email_proof_at = user.email_proof_source = None
        await self.repo.flush()
        EmailProofService.record(user, new_email, "email_link")
        await self.repo.flush()
        await self._notify_old(old_email)

    async def _admit(self, user_id: UUID) -> None:
        try:
            admitted = await self.redis.set(
                f"email_change_rate:{user_id}",
                "1",
                nx=True,
                ex=get_settings().email_verification_resend_cooldown_seconds,
            )
        except RedisError:
            raise HTTPException(503, UNAVAILABLE) from None
        if not admitted:
            raise HTTPException(429, "Please wait before requesting another change")

    @staticmethod
    def _reauthenticate(user: User, password: str | None) -> None:
        """Password accounts re-enter it; OAuth-only accounts need a current email proof."""
        if user.hashed_password:
            if not password or not verify_password(password, user.hashed_password):
                raise HTTPException(403, "Current password is incorrect")
        elif not EmailProofService.has_current_proof(user):
            raise HTTPException(403, "Confirm your current email first")

    async def _issue(self, user: User, new_email: str) -> None:
        cfg = get_settings()
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        user.verification_token = digest
        user.verification_email = new_email
        user.verification_purpose = PURPOSE
        user.verification_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=cfg.email_verification_ttl_seconds
        )
        await self.repo.flush()
        try:
            await self.redis.setex(
                f"email_change:{user.id}:{digest}", cfg.email_verification_ttl_seconds, "1"
            )
            sent = await self.mail.send_email_change_link(new_email, token)
        except (RedisError, HTTPError):
            raise HTTPException(503, UNAVAILABLE) from None
        if not sent:
            raise HTTPException(503, UNAVAILABLE)

    async def _consume(self, user_id: UUID, digest: str) -> bool:
        try:
            return bool(await self.redis.getdel(f"email_change:{user_id}:{digest}"))
        except RedisError:
            raise HTTPException(503, UNAVAILABLE) from None

    @staticmethod
    def _clear_pending(user: User) -> None:
        user.verification_token = user.verification_email = None
        user.verification_purpose = user.verification_expires_at = None

    async def _notify_old(self, old_email: str) -> None:
        try:
            if await self.mail.send_email_changed_notice(old_email):
                return
        except HTTPError:
            pass
        logger.warning("Previous-address notice was not sent")
