"""Network/transport failures during OAuth token validation must never be
treated as proof the token is invalid — only a confirmed GitHub rejection
(401/403) may cause a stored token to be wiped.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.github_oauth_service import GitHubOAuthService
from app.services.gitlab_oauth_service import GitLabOAuthService


class TestGitHubCheckTokenValidityThreeStates:
    """check_token_validity must distinguish valid / rejected / unknown."""

    @pytest.mark.asyncio
    async def test_200_is_valid(self):
        service = GitHubOAuthService()
        resp = httpx.Response(200, request=httpx.Request("GET", "https://api.github.com/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is True

    @pytest.mark.asyncio
    async def test_401_is_confirmed_rejection(self):
        service = GitHubOAuthService()
        resp = httpx.Response(401, request=httpx.Request("GET", "https://api.github.com/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is False

    @pytest.mark.asyncio
    async def test_403_is_confirmed_rejection(self):
        service = GitHubOAuthService()
        resp = httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is False

    @pytest.mark.asyncio
    async def test_network_error_is_unknown_not_invalid(self):
        service = GitHubOAuthService()
        with patch.object(service.client, "get", AsyncMock(side_effect=httpx.ConnectError("boom"))):
            assert await service.check_token_validity("tok") is None

    @pytest.mark.asyncio
    async def test_timeout_is_unknown_not_invalid(self):
        service = GitHubOAuthService()
        with patch.object(
            service.client, "get", AsyncMock(side_effect=httpx.TimeoutException("boom"))
        ):
            assert await service.check_token_validity("tok") is None

    @pytest.mark.asyncio
    async def test_5xx_is_unknown_not_invalid(self):
        service = GitHubOAuthService()
        resp = httpx.Response(503, request=httpx.Request("GET", "https://api.github.com/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is None

    @pytest.mark.asyncio
    async def test_429_is_unknown_not_invalid(self):
        service = GitHubOAuthService()
        resp = httpx.Response(429, request=httpx.Request("GET", "https://api.github.com/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is None


class TestGitLabCheckTokenValidityThreeStates:
    """Same three-state contract applies to the GitLab OAuth service."""

    @pytest.mark.asyncio
    async def test_200_is_valid(self):
        service = GitLabOAuthService()
        resp = httpx.Response(200, request=httpx.Request("GET", "https://gitlab.com/api/v4/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is True

    @pytest.mark.asyncio
    async def test_401_is_confirmed_rejection(self):
        service = GitLabOAuthService()
        resp = httpx.Response(401, request=httpx.Request("GET", "https://gitlab.com/api/v4/user"))
        with patch.object(service.client, "get", AsyncMock(return_value=resp)):
            assert await service.check_token_validity("tok") is False

    @pytest.mark.asyncio
    async def test_network_error_is_unknown_not_invalid(self):
        service = GitLabOAuthService()
        with patch.object(service.client, "get", AsyncMock(side_effect=httpx.ConnectError("boom"))):
            assert await service.check_token_validity("tok") is None


class TestEnsureValidTokenPreservesOnUnknown:
    """ensure_valid_token must never wipe a token when GitHub state is unknown."""

    @pytest.mark.asyncio
    async def test_network_failure_during_refresh_check_keeps_token(self):
        """Regression guard: a transport error must NOT be reported as an
        invalid token — this used to wipe every agent's OAuth token on any
        GitHub outage."""
        service = GitHubOAuthService()
        with (
            patch.object(service, "refresh_token", AsyncMock(return_value=None)),
            patch.object(service.client, "get", AsyncMock(side_effect=httpx.ConnectError("boom"))),
        ):
            result = await service.ensure_valid_token(
                token="live-token",
                refresh_token=None,
                expires_at=0,  # already expired -> forces the check path
            )
        assert result is None, "unknown validity must be treated as 'do not touch'"

    @pytest.mark.asyncio
    async def test_confirmed_401_still_wipes_token(self):
        """A proven GitHub rejection must still clear the token — the fix
        must not turn this into 'never wipe'."""
        service = GitHubOAuthService()
        resp = httpx.Response(401, request=httpx.Request("GET", "https://api.github.com/user"))
        with (
            patch.object(service, "refresh_token", AsyncMock(return_value=None)),
            patch.object(service.client, "get", AsyncMock(return_value=resp)),
        ):
            result = await service.ensure_valid_token(
                token="dead-token",
                refresh_token=None,
                expires_at=0,
            )
        assert result == {"access_token": None, "refresh_token": None, "expires_at": None}
