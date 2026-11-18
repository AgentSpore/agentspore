"""A resend must not invalidate the verification link from the first email."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.redis_client import get_redis
from app.main import app
from app.services.email_service import get_email_service

EMAIL = "newcomer@example.com"


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


@pytest.fixture
def user():
    u = MagicMock()
    u.id = "user-1"
    u.email = EMAIL
    u.is_verified = False
    u.verification_purpose = "registration"
    u.verification_email = EMAIL
    u.verification_token = None
    u.verification_expires_at = None
    return u


@pytest.fixture
def redis():
    return FakeRedis()


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
    assert await send() == first
    resp = await client.get(f"/api/v1/auth/verify-email?token={first}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_link_from_resend_works_too(client, send):
    await send()
    second = await send()
    resp = await client.get(f"/api/v1/auth/verify-email?token={second}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_link_is_harmless_after_verification(client, user, send):
    first = await send()
    second = await send()
    assert (await client.get(f"/api/v1/auth/verify-email?token={first}")).status_code == 200
    user.is_verified = True
    user.verification_token = None
    resp = await client.get(f"/api/v1/auth/verify-email?token={second}")
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
async def test_cooldown_still_silent(client, send, mailer):
    await send()
    mailer.send_verification_email.reset_mock()
    resp = await client.post("/api/v1/auth/resend-verification", json={"email": EMAIL})
    assert resp.status_code == 200
    mailer.send_verification_email.assert_not_called()
