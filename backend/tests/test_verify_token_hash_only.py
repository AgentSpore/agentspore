"""Verification tokens must not be recoverable from a Redis dump."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.verification_token import VerificationToken
from app.main import app
from app.services.email_service import get_email_service
from tests.test_resend_verification_keeps_first_link import FakeRedis

EMAIL = "newcomer@example.com"
TTL = get_settings().email_verification_ttl_seconds
LEGACY = "legacy-token-" + "x" * 30  # 43 chars, shape of the old secrets.token_urlsafe(32)
OTHER_KEY = "another-secret-key-for-tests-only-0123456789"


class World:
    def __init__(self) -> None:
        self.redis = FakeRedis()
        self.mailer = AsyncMock()
        self.mailer.send_verification_email = AsyncMock(return_value=True)
        self.created: list = []
        self.client: AsyncClient | None = None

    def emailed(self) -> str:
        return self.mailer.send_verification_email.call_args.args[1]

    async def resend(self) -> str:
        self.redis.counters.clear()
        resp = await self.client.post("/api/v1/auth/resend-verification", json={"email": EMAIL})
        assert resp.status_code == 200
        return self.emailed()

    async def verify(self, token: str) -> int:
        resp = await self.client.get("/api/v1/auth/verify-email", params={"token": token})
        return resp.status_code

    def dump(self) -> list[str]:
        return list(self.redis.data) + [str(v) for v in self.redis.data.values()]

    async def seed_legacy_link(self) -> object:
        """Account whose only link was emailed before token hashing."""
        user = self.created[0]
        self.redis.data.clear()
        user.verification_token = VerificationToken.digest(LEGACY)
        user.verification_expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL)
        await self.redis.setex(VerificationToken.legacy_link_key(LEGACY), TTL, "new-user")
        return user


@pytest.fixture
async def world():
    w = World()
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda u: (setattr(u, "id", "new-user"), w.created.append(u)))
    lookup = MagicMock()
    lookup.scalar_one_or_none.side_effect = lambda: w.created[0] if w.created else None
    db.execute.return_value = lookup

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: w.redis
    app.dependency_overrides[get_email_service] = lambda: w.mailer
    with (
        patch("app.api.v1.auth.get_welcome_reward_service", return_value=AsyncMock()),
        patch("app.api.v1.auth.FunnelRepository", return_value=AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            w.client = c
            reg = await c.post(
                "/api/v1/auth/register",
                json={"email": EMAIL, "password": "S3cure-Passw0rd!x", "name": "New"},
            )
            assert reg.status_code == 202
            yield w
    app.dependency_overrides.clear()


def assert_token_absent(w: World, token: str) -> None:
    assert not any(token in item for item in w.dump())


@pytest.mark.asyncio
async def test_redis_holds_no_raw_token_after_register_and_resend(world):
    first = world.emailed()
    assert_token_absent(world, first)
    assert await world.resend() == first
    assert_token_absent(world, first)


@pytest.mark.asyncio
async def test_link_key_is_hash_of_token(world):
    assert VerificationToken.link_key(world.emailed()) in world.redis.data


@pytest.mark.asyncio
async def test_verify_with_hashed_key_is_single_use(world):
    first = world.emailed()
    assert await world.verify(first) == 200
    assert await world.verify(first) == 400


@pytest.mark.asyncio
async def test_legacy_raw_key_link_verifies_once(world):
    await world.seed_legacy_link()
    assert await world.verify(LEGACY) == 200
    assert await world.verify(LEGACY) == 400


@pytest.mark.asyncio
async def test_legacy_raw_pointer_issues_fresh_token(world):
    user = await world.seed_legacy_link()
    await world.redis.setex("email_verify_user:new-user", TTL, LEGACY)
    fresh = await world.resend()
    assert fresh != LEGACY
    assert user.verification_token == VerificationToken.digest(fresh)
    assert_token_absent(world, fresh)
    assert await world.verify(fresh) == 200


@pytest.mark.asyncio
async def test_tampered_token_rejected(world):
    first = world.emailed()
    tampered = first[:-1] + ("A" if first[-1] != "A" else "B")
    assert await world.verify(tampered) == 400
    assert await world.verify(first) == 200


@pytest.mark.asyncio
async def test_token_from_other_secret_key_rejected(world):
    first = world.emailed()
    nonce = world.redis.data["email_verify_user:new-user"]
    other = get_settings().model_copy(update={"secret_key": OTHER_KEY})
    with patch("app.core.verification_token.get_settings", return_value=other):
        forged = VerificationToken.from_nonce(nonce)
    assert forged != first
    assert await world.verify(forged) == 400


@pytest.mark.asyncio
async def test_digest_of_token_cannot_consume_real_link(world):
    """The hash seen in a DB or Redis dump must not burn the real link via the legacy path."""
    first = world.emailed()
    assert await world.verify(VerificationToken.digest(first)) == 400
    assert await world.verify(first) == 200
