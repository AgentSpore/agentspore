"""Prove ownership of a Solana address by signing a one-time challenge with its wallet."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends
from loguru import logger
from redis.asyncio import Redis
from solders.pubkey import Pubkey
from solders.signature import Signature
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.payout_repo import PayoutRepository

CHALLENGE_TTL_SECONDS = 600
RATE_WINDOW_SECONDS = 3600


class ChallengeExpiredError(Exception):
    """No live challenge for this account and address."""


class InvalidSignatureError(Exception):
    """The signature does not verify against the address."""


class RateLimitedError(Exception):
    """Too many challenges requested."""


class SolanaWalletClaimService:
    def __init__(self, db: AsyncSession, redis: Redis, rate_limit: int):
        self.repo = PayoutRepository(db)
        self.redis = redis
        self.rate_limit = rate_limit

    @staticmethod
    def _challenge_key(user_id: str, wallet: str) -> str:
        return f"solana_claim:{user_id}:{wallet}"

    async def issue_challenge(self, user_id: str, wallet: str) -> tuple[str, datetime]:
        rate_key = f"solana_claim_rate:{user_id}"
        count = await self.redis.incr(rate_key)
        if count == 1:
            await self.redis.expire(rate_key, RATE_WINDOW_SECONDS)
        if count > self.rate_limit:
            raise RateLimitedError
        issued = datetime.now(UTC)
        message = (
            "AgentSpore: confirm this wallet belongs to you\n"
            f"Wallet: {wallet}\n"
            f"Account: {user_id}\n"
            f"Nonce: {secrets.token_urlsafe(16)}\n"
            f"Issued: {issued.isoformat()}"
        )
        await self.redis.set(
            self._challenge_key(user_id, wallet), message, ex=CHALLENGE_TTL_SECONDS
        )
        return message, issued + timedelta(seconds=CHALLENGE_TTL_SECONDS)

    async def claim(self, user_id: str, wallet: str, signature: bytes) -> None:
        """Consume the challenge, verify the signature, then move the address; caller commits."""
        message = await self.redis.getdel(self._challenge_key(user_id, wallet))
        if message is None:
            raise ChallengeExpiredError
        if isinstance(message, bytes):
            message = message.decode()
        if not Signature.from_bytes(signature).verify(Pubkey.from_string(wallet), message.encode()):
            raise InvalidSignatureError
        await self.repo.move_solana_wallet(user_id, wallet)
        logger.info("Solana wallet claimed by signature for user {}", user_id)


def get_solana_wallet_claim_service(
    db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)
) -> SolanaWalletClaimService:
    return SolanaWalletClaimService(db, redis, get_settings().solana_wallet_challenge_rate_limit)
