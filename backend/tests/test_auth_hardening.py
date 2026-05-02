"""Tests for auth hardening: email verification, rate limits, brute force protection.

Unit tests use AsyncMock for DB/Redis — no Docker required.
Integration tests are marked @pytest.mark.integration and require Docker.
"""

from __future__ import annotations

import hashlib
import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_user(*, is_verified: bool = True, hashed_password: str = "hashed") -> MagicMock:
    user = MagicMock()
    user.id = "test-user-uuid"
    user.email = "test@example.com"
    user.hashed_password = hashed_password
    user.is_verified = is_verified
    user.verification_token = None
    user.verification_expires_at = None
    return user


def _make_redis(
    *,
    get_return: str | bytes | None = None,
    incr_return: int = 1,
    exists_return: int = 0,
) -> AsyncMock:
    redis = AsyncMock()
    redis.get.return_value = get_return
    redis.incr.return_value = incr_return
    redis.expire.return_value = True
    redis.exists.return_value = exists_return
    redis.setex.return_value = True
    redis.delete.return_value = 1
    return redis


# ── email verification token logic ───────────────────────────────────────────


class TestTokenGeneration:
    """Unit tests for token generation properties."""

    def test_token_is_urlsafe(self):
        token = secrets.token_urlsafe(32)
        assert len(token) >= 40  # 32 bytes → 43 chars base64url
        # No characters that break URL query params
        for ch in "+/=":
            assert ch not in token

    def test_token_hash_deterministic(self):
        token = secrets.token_urlsafe(32)
        h1 = hashlib.sha256(token.encode()).hexdigest()
        h2 = hashlib.sha256(token.encode()).hexdigest()
        assert h1 == h2

    def test_tokens_are_unique(self):
        tokens = {secrets.token_urlsafe(32) for _ in range(1000)}
        assert len(tokens) == 1000

    def test_token_hashes_differ_from_token(self):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        assert token != token_hash

    def test_hash_length_is_64_chars(self):
        token_hash = hashlib.sha256(b"test").hexdigest()
        assert len(token_hash) == 64


# ── register endpoint ─────────────────────────────────────────────────────────


class TestRegisterRateLimit:
    """Register should be blocked after 3 attempts per IP."""

    @pytest.mark.asyncio
    async def test_register_blocked_at_4th_attempt(self):
        """4th attempt from same IP returns 429."""
        from app.api.v1.auth import _check_ip_rate_limit

        redis = _make_redis(incr_return=4)
        with pytest.raises(HTTPException) as exc_info:
            await _check_ip_rate_limit(redis, "register_rate:1.2.3.4", limit=3, window_seconds=3600)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_register_allowed_at_3rd_attempt(self):
        """3rd attempt from same IP is still allowed."""
        from app.api.v1.auth import _check_ip_rate_limit

        redis = _make_redis(incr_return=3)
        # Should not raise
        await _check_ip_rate_limit(redis, "register_rate:1.2.3.4", limit=3, window_seconds=3600)

    @pytest.mark.asyncio
    async def test_expire_set_on_first_increment(self):
        """TTL must be set when counter starts (INCR returns 1)."""
        from app.api.v1.auth import _check_ip_rate_limit

        redis = _make_redis(incr_return=1)
        await _check_ip_rate_limit(redis, "register_rate:1.2.3.4", limit=3, window_seconds=3600)
        redis.expire.assert_called_once_with("register_rate:1.2.3.4", 3600)

    @pytest.mark.asyncio
    async def test_expire_not_set_on_subsequent_increments(self):
        """TTL must NOT be reset on subsequent increments (would slide the window)."""
        from app.api.v1.auth import _check_ip_rate_limit

        redis = _make_redis(incr_return=2)
        await _check_ip_rate_limit(redis, "register_rate:1.2.3.4", limit=3, window_seconds=3600)
        redis.expire.assert_not_called()


# ── login brute force ─────────────────────────────────────────────────────────


class TestLoginBruteForce:
    """Login should be blocked after 5 failed attempts per IP."""

    @pytest.mark.asyncio
    async def test_login_blocked_after_5_failures(self):
        """IP with 5 recorded failures gets 429 before even checking credentials."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.agent_service import get_agent_service

        mock_redis = _make_redis(get_return="5")  # existing counter = 5
        mock_db = AsyncMock()
        mock_agent_svc = AsyncMock()

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_agent_service] = lambda: mock_agent_svc

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "victim@example.com", "password": "wrongpass"},
                )
            assert resp.status_code == 429
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_login_fail_increments_counter(self):
        """A wrong password increments the Redis failure counter."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.agent_service import get_agent_service

        mock_redis = _make_redis(get_return="0", incr_return=1)

        # DB returns no user (wrong email)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        mock_agent_svc = AsyncMock()

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_agent_service] = lambda: mock_agent_svc

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "nobody@example.com", "password": "wrongpass"},
                )
            assert resp.status_code == 401
            mock_redis.incr.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_successful_login_clears_fail_counter(self):
        """Correct credentials delete the failure counter from Redis."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.agent_service import get_agent_service

        from app.core.security import get_password_hash

        real_hash = get_password_hash("CorrectPass1")
        user = _make_user(is_verified=True, hashed_password=real_hash)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        mock_redis = _make_redis(get_return="2")  # 2 previous failures
        mock_agent_svc = AsyncMock()
        mock_agent_svc.link_agents_by_email = AsyncMock()

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_agent_service] = lambda: mock_agent_svc

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "test@example.com", "password": "CorrectPass1"},
                )
            assert resp.status_code == 200
            # httpx test client reports 127.0.0.1 as client host
            mock_redis.delete.assert_any_call("login_fail:127.0.0.1")
        finally:
            app.dependency_overrides.clear()


# ── email verification gate ───────────────────────────────────────────────────


class TestLoginVerificationGate:
    """Login must be blocked for unverified accounts."""

    @pytest.mark.asyncio
    async def test_login_blocked_if_not_verified(self):
        """Correct credentials but unverified → 403."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.agent_service import get_agent_service
        from app.core.security import get_password_hash

        real_hash = get_password_hash("CorrectPass1")
        user = _make_user(is_verified=False, hashed_password=real_hash)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        mock_redis = _make_redis(get_return="0")
        mock_agent_svc = AsyncMock()

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_agent_service] = lambda: mock_agent_svc

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "test@example.com", "password": "CorrectPass1"},
                )
            assert resp.status_code == 403
            assert "verify" in resp.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()


# ── verify-email endpoint ─────────────────────────────────────────────────────


class TestVerifyEmailEndpoint:
    """GET /auth/verify-email?token=..."""

    @pytest.mark.asyncio
    async def test_valid_token_verifies_user(self):
        """Valid token marks user verified and returns JWT pair."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis

        user = _make_user(is_verified=False)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        mock_redis = _make_redis(get_return="test-user-uuid")

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/v1/auth/verify-email?token=somevalidtoken")
            assert resp.status_code == 200
            body = resp.json()
            assert "access_token" in body
            assert body["access_token"] != ""
            # Token was deleted (single-use)
            mock_redis.delete.assert_called_once_with("email_verify:somevalidtoken")
            # User marked verified
            assert user.is_verified is True
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_invalid_token_returns_400(self):
        """Unknown token → 400."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis

        mock_db = AsyncMock()
        mock_redis = _make_redis(get_return=None)  # token not in Redis

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/v1/auth/verify-email?token=bogus")
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_already_verified_is_idempotent(self):
        """Re-verification of an already-verified account returns 200, not error."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis

        user = _make_user(is_verified=True)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        mock_redis = _make_redis(get_return="test-user-uuid")

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/v1/auth/verify-email?token=sometoken")
            assert resp.status_code == 200
            assert "already verified" in resp.json()["message"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_token_single_use(self):
        """Token is deleted from Redis after use, cannot be replayed."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis

        user = _make_user(is_verified=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        mock_redis = _make_redis(get_return="test-user-uuid")

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/v1/auth/verify-email?token=tok123")
            assert resp.status_code == 200
            # Verify Redis delete was called with correct key
            mock_redis.delete.assert_called_with("email_verify:tok123")
        finally:
            app.dependency_overrides.clear()


# ── resend verification rate limit ────────────────────────────────────────────


class TestResendVerificationRateLimit:
    """Resend must be silently rate-limited to 1/min per email."""

    @pytest.mark.asyncio
    async def test_second_resend_within_cooldown_is_silent(self):
        """2nd resend within 60s returns 200 with generic message but does NOT send email."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.email_service import get_email_service

        user = _make_user(is_verified=False)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        # incr_return=2 simulates second call within cooldown window
        mock_redis = _make_redis(incr_return=2)

        mock_email_svc = AsyncMock()
        mock_email_svc.send_verification_email = AsyncMock(return_value=True)

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_email_service] = lambda: mock_email_svc

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/resend-verification",
                    json={"email": "test@example.com"},
                )
            assert resp.status_code == 200
            mock_email_svc.send_verification_email.assert_not_called()
        finally:
            app.dependency_overrides.clear()


# ── password reset token properties ──────────────────────────────────────────


class TestPasswordResetToken:
    """Password reset tokens must be single-use and expire after 1h."""

    @pytest.mark.asyncio
    async def test_reset_token_deleted_after_use(self):
        """Token is removed from Redis after password is changed."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis

        user = _make_user()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result
        mock_redis = _make_redis(get_return="test-user-uuid")

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/reset-password",
                    json={"token": "validtoken", "new_password": "NewPass123"},
                )
            assert resp.status_code == 200
            mock_redis.delete.assert_called_with("password_reset:validtoken")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_expired_reset_token_returns_400(self):
        """Expired/invalid token returns 400."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis

        mock_db = AsyncMock()
        mock_redis = _make_redis(get_return=None)

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/reset-password",
                    json={"token": "expiredtoken", "new_password": "NewPass123"},
                )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_forgot_password_rate_limit_silent(self):
        """Exceeding forgot-password rate limit returns 200 (no info leak)."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.email_service import get_email_service

        user = _make_user()
        user.hashed_password = "hashed"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        # 4th request → over limit of 3
        mock_redis = _make_redis(incr_return=4)
        mock_email_svc = AsyncMock()

        async def override_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_email_service] = lambda: mock_email_svc

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/auth/forgot-password",
                    json={"email": "test@example.com"},
                )
            assert resp.status_code == 200
            mock_email_svc.send_password_reset.assert_not_called()
        finally:
            app.dependency_overrides.clear()


# ── client IP extraction ──────────────────────────────────────────────────────


class TestClientIPExtraction:
    """_client_ip must use X-Forwarded-For when present."""

    def test_uses_x_forwarded_for(self):
        from app.api.v1.auth import _client_ip

        request = MagicMock()
        request.headers = {"x-forwarded-for": "203.0.113.1, 10.0.0.1"}
        request.client = None

        assert _client_ip(request) == "203.0.113.1"

    def test_falls_back_to_client_host(self):
        from app.api.v1.auth import _client_ip

        request = MagicMock()
        request.headers = {}
        request.client.host = "192.168.1.5"

        assert _client_ip(request) == "192.168.1.5"

    def test_handles_missing_client(self):
        from app.api.v1.auth import _client_ip

        request = MagicMock()
        request.headers = {}
        request.client = None

        assert _client_ip(request) == "unknown"
