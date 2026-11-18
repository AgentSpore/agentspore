"""A resend must not invalidate the verification link from the first email."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.main import app
from app.services.email_service import get_email_service

EMAIL = "newcomer@example.com"
TTL = get_settings().email_verification_ttl_seconds
SEED_TOKEN = "seeded-first-email-token"


class FakeRedis:
    """In-memory stand-in for the redis commands the auth flow uses (no TTL clock)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def setex(self, key, ttl, value):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def getdel(self, key):
        return self.data.pop(key, None)

    async def exists(self, key):
        return int(key in self.data)

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key, ttl):
        return True

    async def delete(self, key):
        return int(self.data.pop(key, None) is not None)


@pytest.fixture
def user():
    u = MagicMock()
    u.id = "user-1"
    u.email = EMAIL
    u.is_verified = False
    u.verification_purpose = "registration"
    u.verification_email = EMAIL
    u.verification_token = hashlib.sha256(SEED_TOKEN.encode()).hexdigest()
    u.verification_expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL)
    return u


@pytest.fixture
async def redis(user):
    """State register() leaves behind: token key plus per-user pointer."""
    r = FakeRedis()
    await r.setex(f"email_verify:{SEED_TOKEN}", TTL, user.id)
    await r.setex(f"email_verify_user:{user.id}", TTL, SEED_TOKEN)
    return r


@pytest.fixture
def mailer():
    svc = AsyncMock()
    svc.send_verification_email = AsyncMock(return_value=True)
    return svc


@pytest.fixture
async def client(user, redis, mailer):
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = AsyncMock()
    db.execute.return_value = result

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_email_service] = lambda: mailer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def send(client, redis, mailer):
    """POST resend-verification past the cooldown; return the token that was emailed."""

    async def _send() -> str:
        redis.counters.clear()
        mailer.send_verification_email.reset_mock()
        resp = await client.post("/api/v1/auth/resend-verification", json={"email": EMAIL})
        assert resp.status_code == 200
        return mailer.send_verification_email.call_args.args[1]

    return _send


@pytest.mark.asyncio
async def test_first_link_works_after_resend(client, send):
    first = await send()
    assert first == SEED_TOKEN
    assert await send() == first
    resp = await client.get(f"/api/v1/auth/verify-email?token={first}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_verification_removes_per_user_pointer(client, user, redis, send):
    first = await send()
    assert f"email_verify_user:{user.id}" in redis.data
    assert (await client.get(f"/api/v1/auth/verify-email?token={first}")).status_code == 200
    assert f"email_verify_user:{user.id}" not in redis.data
    assert not any(first == v for v in redis.data.values())


@pytest.mark.asyncio
async def test_pointer_cleanup_failure_does_not_fail_verification(client, redis, send):
    first = await send()
    redis.delete = AsyncMock(side_effect=RedisError("down"))
    resp = await client.get(f"/api/v1/auth/verify-email?token={first}")
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_link_is_harmless_after_verification(client, user, redis, send):
    """Token still live in Redis, but the account is verified: the guard rejects it."""
    first = await send()
    user.is_verified = True
    assert f"email_verify:{first}" in redis.data
    resp = await client.get(f"/api/v1/auth/verify-email?token={first}")
    assert resp.status_code == 400
    assert "access_token" not in resp.json()


@pytest.mark.asyncio
async def test_expired_token_fails_and_resend_issues_fresh_one(client, user, send):
    first = await send()
    user.verification_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert (await client.get(f"/api/v1/auth/verify-email?token={first}")).status_code == 400
    fresh = await send()
    assert fresh != first
    assert user.verification_token == hashlib.sha256(fresh.encode()).hexdigest()


@pytest.mark.asyncio
async def test_nearly_expired_token_is_not_resent(client, user, send):
    user.verification_expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL / 2 - 60)
    fresh = await send()
    assert fresh != SEED_TOKEN
    assert user.verification_token == hashlib.sha256(fresh.encode()).hexdigest()
    assert user.verification_expires_at > datetime.now(timezone.utc) + timedelta(seconds=TTL - 60)


@pytest.mark.asyncio
async def test_cooldown_still_silent(client, send, mailer):
    await send()
    mailer.send_verification_email.reset_mock()
    resp = await client.post("/api/v1/auth/resend-verification", json={"email": EMAIL})
    assert resp.status_code == 200
    mailer.send_verification_email.assert_not_called()


@pytest.mark.asyncio
async def test_register_then_resend_keeps_registration_link(mailer):
    """Real register(): its emailed token survives a resend and verifies."""
    created = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda u: (setattr(u, "id", "new-user"), created.append(u)))
    lookup = MagicMock()
    lookup.scalar_one_or_none.side_effect = lambda: created[0] if created else None
    db.execute.return_value = lookup

    async def override_db():
        yield db

    fresh_redis = FakeRedis()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: fresh_redis
    app.dependency_overrides[get_email_service] = lambda: mailer
    try:
        with (
            patch("app.api.v1.auth.get_welcome_reward_service", return_value=AsyncMock()),
            patch("app.api.v1.auth.FunnelRepository", return_value=AsyncMock()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                reg = await c.post(
                    "/api/v1/auth/register",
                    json={"email": EMAIL, "password": "S3cure-Passw0rd!x", "name": "New"},
                )
                assert reg.status_code == 202
                first = mailer.send_verification_email.call_args.args[1]
                mailer.send_verification_email.reset_mock()
                resend = await c.post("/api/v1/auth/resend-verification", json={"email": EMAIL})
                assert resend.status_code == 200
                assert mailer.send_verification_email.call_args.args[1] == first
                assert (await c.get(f"/api/v1/auth/verify-email?token={first}")).status_code == 200
    finally:
        app.dependency_overrides.clear()
