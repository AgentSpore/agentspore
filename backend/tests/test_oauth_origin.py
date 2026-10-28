"""OAuth runs through the one registered callback host and returns to the origin domain."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1 import oauth


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": "https",
            "method": "GET",
            "path": "/",
            "headers": [(b"host", host.encode())],
        }
    )


@pytest.fixture(autouse=True)
def _domains(monkeypatch):
    monkeypatch.setattr(oauth.settings, "domains", ["agentspore.com", "agentspore.ru"])
    monkeypatch.setattr(oauth.settings, "oauth_redirect_base_url", "https://agentspore.com")
    monkeypatch.setattr(oauth.settings, "user_github_oauth_client_id", "cid")


@pytest.mark.asyncio
async def test_login_on_ru_keeps_canonical_callback_and_remembers_origin():
    redis = AsyncMock()
    response = await oauth.github_login(_request("agentspore.ru"), redis=redis, origin=None)
    assert (
        response.headers["location"]
        == "https://agentspore.com/api/v1/oauth/github?origin=agentspore.ru"
    )
    redis.setex.assert_not_called()
    response = await oauth.github_login(
        _request("agentspore.com"), redis=redis, origin="agentspore.ru"
    )
    location = response.headers["location"]
    assert "redirect_uri=https://agentspore.com/api/v1/oauth/github/callback" in location
    origin_calls = [c for c in redis.setex.call_args_list if c.args[0].startswith("oauth_origin:")]
    assert [c.args[2] for c in origin_calls] == ["agentspore.ru"]


@pytest.mark.asyncio
async def test_login_from_unlisted_host_stores_no_origin():
    redis = AsyncMock()
    await oauth.github_login(_request("evil.example"), redis=redis, origin=None)
    assert all(not c.args[0].startswith("oauth_origin:") for c in redis.setex.call_args_list)


@pytest.mark.asyncio
async def test_consume_origin_returns_only_allowlisted_domain():
    redis = AsyncMock()
    redis.getdel.return_value = b"agentspore.ru"
    assert await oauth._consume_oauth_origin(redis, "s", "github") == "agentspore.ru"
    redis.getdel.return_value = "evil.example"
    assert await oauth._consume_oauth_origin(redis, "s", "github") is None
    redis.getdel.return_value = None
    assert await oauth._consume_oauth_origin(redis, "s", "github") is None


def test_token_redirect_targets_origin_domain():
    response = oauth._make_token_response_redirect("u1", "agentspore.ru")
    assert response.headers["location"].startswith("https://agentspore.ru/auth/callback?access_token=")


def test_token_redirect_without_origin_uses_default_frontend():
    response = oauth._make_token_response_redirect("u1", None)
    assert response.headers["location"].startswith(f"{oauth.FRONTEND_URL}/auth/callback?")


@pytest.mark.asyncio
async def test_invalid_state_still_rejected_before_origin_lookup():
    redis = AsyncMock()
    redis.delete.return_value = 0
    with pytest.raises(HTTPException):
        await oauth.github_callback(
            code="c", request=_request("agentspore.com"), db=AsyncMock(), state="bad", redis=redis
        )
    redis.getdel.assert_not_called()
