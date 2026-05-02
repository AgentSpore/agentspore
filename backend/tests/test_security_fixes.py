"""Security fix regression tests (2026-05-03 audit).

Covers:
- C1: OAuth CSRF state — invalid/missing state → 400
- H1/H2: client_ip() trusted-proxy logic
- H3: HostedAgentFileWrite max_length=50_000
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from pydantic import ValidationError


# ── H3: schema field size ────────────────────────────────────────────────────


def test_agent_file_write_max_length_50k():
    from app.schemas.hosted_agents import AgentFileWriteRequest

    valid = AgentFileWriteRequest(file_path="foo.py", content="x" * 50_000)
    assert len(valid.content) == 50_000


def test_agent_file_write_rejects_over_50k():
    from app.schemas.hosted_agents import AgentFileWriteRequest

    with pytest.raises(ValidationError):
        AgentFileWriteRequest(file_path="foo.py", content="x" * 50_001)


def test_agent_file_batch_item_max_length_50k():
    from app.schemas.hosted_agents import AgentFileBatchItem

    with pytest.raises(ValidationError):
        AgentFileBatchItem(file_path="foo.py", content="x" * 50_001)


# ── H2: client_ip trusted-proxy logic ───────────────────────────────────────


def _make_request(*, client_host: str, xff: str | None = None) -> MagicMock:
    req = MagicMock(spec=Request)
    req.client = MagicMock()
    req.client.host = client_host
    headers: dict[str, str] = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    req.headers = headers
    return req


def _call_client_ip(request: MagicMock, trusted: list[str]) -> str:
    """Call client_ip() with patched trusted_proxy_ips setting."""
    from app.api.deps import client_ip

    mock_settings = MagicMock()
    mock_settings.trusted_proxy_ips = trusted
    with patch("app.api.deps.get_settings", return_value=mock_settings):
        return client_ip(request)


class TestClientIp:
    def test_direct_connection_no_xff(self):
        req = _make_request(client_host="1.2.3.4")
        ip = _call_client_ip(req, trusted=["127.0.0.1"])
        assert ip == "1.2.3.4"

    def test_trusted_proxy_honours_xff(self):
        req = _make_request(client_host="127.0.0.1", xff="203.0.113.5, 10.0.0.1")
        ip = _call_client_ip(req, trusted=["127.0.0.1"])
        assert ip == "203.0.113.5"

    def test_untrusted_proxy_ignores_xff(self):
        """Direct client not in trusted list — XFF must be ignored (spoof prevention)."""
        req = _make_request(client_host="1.2.3.4", xff="9.9.9.9")
        ip = _call_client_ip(req, trusted=["127.0.0.1"])
        # Raw host returned, not spoofed XFF
        assert ip == "1.2.3.4"

    def test_trusted_cidr_honours_xff(self):
        req = _make_request(client_host="172.18.0.5", xff="5.6.7.8")
        ip = _call_client_ip(req, trusted=["172.16.0.0/12"])
        assert ip == "5.6.7.8"

    def test_unknown_client_returns_unknown(self):
        req = MagicMock(spec=Request)
        req.client = None
        req.headers = {}
        result = _call_client_ip(req, trusted=["127.0.0.1"])
        assert result == "unknown"

    def test_trusted_proxy_empty_xff_falls_back_to_host(self):
        req = _make_request(client_host="127.0.0.1", xff="")
        ip = _call_client_ip(req, trusted=["127.0.0.1"])
        assert ip == "127.0.0.1"


# ── C1: OAuth CSRF state helpers ─────────────────────────────────────────────


class TestOAuthState:
    @pytest.mark.asyncio
    async def test_store_and_consume_state_valid(self):
        from app.api.v1.oauth import _consume_oauth_state, _store_oauth_state

        redis = AsyncMock()
        redis.delete.return_value = 1  # key existed and was deleted

        await _store_oauth_state(redis, "test_state")
        redis.setex.assert_called_once_with("oauth_state:test_state", 300, "1")

        result = await _consume_oauth_state(redis, "test_state")
        assert result is True

    @pytest.mark.asyncio
    async def test_consume_missing_state_returns_false(self):
        from app.api.v1.oauth import _consume_oauth_state

        redis = AsyncMock()
        redis.delete.return_value = 0  # key did not exist

        result = await _consume_oauth_state(redis, "nonexistent_state")
        assert result is False

    @pytest.mark.asyncio
    async def test_consume_is_single_use(self):
        """Second consume of same state must fail (simulated by delete returning 0)."""
        from app.api.v1.oauth import _consume_oauth_state

        redis = AsyncMock()
        redis.delete.side_effect = [1, 0]  # first call deletes, second finds nothing

        assert await _consume_oauth_state(redis, "state_xyz") is True
        assert await _consume_oauth_state(redis, "state_xyz") is False

    @pytest.mark.asyncio
    async def test_google_callback_rejects_invalid_state(self):
        """google_callback with bad state must 400 before touching OAuth provider."""
        from app.api.v1.oauth import google_callback

        redis = AsyncMock()
        redis.delete.return_value = 0  # state not found

        db = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await google_callback(code="some_code", db=db, state="bad_state", redis=redis)

        exc = exc_info.value
        assert exc.status_code == 400
        assert "state" in exc.detail.lower()

    @pytest.mark.asyncio
    async def test_github_callback_rejects_invalid_state(self):
        """github_callback with bad state must 400."""
        from app.api.v1.oauth import github_callback

        redis = AsyncMock()
        redis.delete.return_value = 0

        db = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await github_callback(code="some_code", db=db, state="bad_state", redis=redis)

        exc = exc_info.value
        assert exc.status_code == 400
        assert "state" in exc.detail.lower()
