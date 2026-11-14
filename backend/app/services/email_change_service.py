"""Change the account email through a mailbox-proven, single-use confirmation."""

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from functools import partial
from uuid import UUID

from fastapi import BackgroundTasks, HTTPException
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
OLD_PURPOSE = "email_change_old"
INVALID_LINK = "Invalid or expired email change link"
UNAVAILABLE = "Email change is temporarily unavailable"
GENERIC = "If the address can be used, a confirmation link was sent to it."
APPROVE_FIRST = "Approve the change from your current address first, then confirm the new one."
APPROVED = "Approved. If the new address can be used, a confirmation link was sent to it."
REWARD_PENDING = (
    "Your welcome reward is being processed; you can change your email after it is paid"
)


class EmailChangeService:
    """The old address stays active until the new one proves ownership.

    Accounts without a password first confirm through the current address, so a stolen
    session alone cannot move the account to a mailbox the attacker controls. Mail goes out
    from a background task, after the pending change is committed.
    """

    def __init__(
        self,
        repo: EmailProofRepository,
        redis: Redis,
        mail: EmailService,
        background: BackgroundTasks,
    ):
        self.repo, self.redis, self.mail, self.background = repo, redis, mail, background

    async def request(self, user_id: UUID, new_email: str, password: str | None) -> str:
        """Start a change; the reply never depends on whether the target address is registered."""
        user = await self.repo.lock_user(user_id)
        if user is None or not user.is_verified:
            raise HTTPException(403, "A verified account is required")
        await self._admit(user_id)
        self._reauthenticate(user, password)
        if await self.repo.welcome_reward_unpaid(user_id):
            raise HTTPException(409, REWARD_PENDING)
        if WelcomeBeneficiaryService.is_disposable_domain(new_email.rpartition("@")[2]):
            raise HTTPException(422, "Disposable email addresses are not accepted")
        if not user.hashed_password:
            # The reply and the mail depend on the account type only; taken or unchanged
            # targets are decided after the current address approved.
            await self._issue(user, new_email, OLD_PURPOSE)
            return APPROVE_FIRST
        if new_email == user.email.lower() or await self.repo.email_taken(new_email, user_id):
            return GENERIC
        await self._issue(user, new_email, PURPOSE)
        return GENERIC

    async def confirm(self, user_id: UUID, token: str) -> str:
        """Approve from the old address (step one) or switch to the new one (final step)."""
        user = await self.repo.lock_user(user_id)
        digest = hashlib.sha256(token.encode()).hexdigest()
        pending = self._pending_matches(user, digest) if user else None
        if user is None or pending is None:
            raise HTTPException(400, INVALID_LINK)
        if await self.repo.welcome_reward_unpaid(user_id):
            raise HTTPException(409, REWARD_PENDING)
        purpose, new_email = pending
        if not await self._consume(user_id, purpose, digest):
            raise HTTPException(400, INVALID_LINK)
        unusable = new_email == user.email.lower() or await self.repo.email_taken(
            new_email, user_id
        )
        if purpose == OLD_PURPOSE:
            if unusable:
                self._clear_pending(user)
            else:
                await self._issue(user, new_email, PURPOSE)
            return APPROVED
        if unusable:
            raise HTTPException(400, INVALID_LINK)
        old_email = user.email
        user.email = new_email
        self._clear_pending(user)
        user.email_proof_email = user.email_proof_at = user.email_proof_source = None
        await self.repo.flush()
        EmailProofService.record(user, new_email, "email_link")
        await self.repo.flush()
        self.background.add_task(self._deliver, self.mail.send_email_changed_notice, old_email)
        return "Email changed."

    @staticmethod
    def _pending_matches(user: User, digest: str) -> tuple[str, str] | None:
        """(purpose, new email) when the stored pending change matches the token digest."""
        purpose, new_email = user.verification_purpose, user.verification_email
        if (
            user.is_verified
            and purpose in (PURPOSE, OLD_PURPOSE)
            and new_email
            and user.verification_token
            and secrets.compare_digest(user.verification_token, digest)
            and user.verification_expires_at
            and user.verification_expires_at > datetime.now(timezone.utc)
        ):
            return purpose, new_email
        return None

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

    async def _issue(self, user: User, new_email: str, purpose: str) -> None:
        """Store a hashed single-use token; the link goes to the new or the current address."""
        cfg = get_settings()
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        user.verification_token = digest
        user.verification_email = new_email
        user.verification_purpose = purpose
        user.verification_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=cfg.email_verification_ttl_seconds
        )
        await self.repo.flush()
        try:
            await self.redis.setex(
                f"{purpose}:{user.id}:{digest}", cfg.email_verification_ttl_seconds, "1"
            )
        except RedisError:
            raise HTTPException(503, UNAVAILABLE) from None
        if purpose == PURPOSE:
            send = partial(self.mail.send_email_change_link, new_email, token)
        else:
            send = partial(self.mail.send_email_change_old_link, user.email, token, new_email)
        self.background.add_task(self._deliver, send)

    async def _consume(self, user_id: UUID, purpose: str, digest: str) -> bool:
        try:
            return bool(await self.redis.getdel(f"{purpose}:{user_id}:{digest}"))
        except RedisError:
            raise HTTPException(503, UNAVAILABLE) from None

    @staticmethod
    def _clear_pending(user: User) -> None:
        user.verification_token = user.verification_email = None
        user.verification_purpose = user.verification_expires_at = None

    @staticmethod
    async def _deliver(send: Callable[..., Awaitable[bool]], *args: str) -> None:
        """Mail failures are logged only: the reply must not depend on the provider."""
        try:
            if await send(*args):
                return
        except HTTPError:
            pass
        logger.warning("Email change mail was not sent")
