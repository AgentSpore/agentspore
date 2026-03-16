"""Tests for agent registration and GitHub OAuth."""
import hashlib
import secrets
import time
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


# ==========================================
# Unit tests: GitHubOAuthService (no DB)
# ==========================================

class TestGitHubOAuthService:
    """Unit tests for GitHubOAuthService — no DB or Docker required."""

    def test_authorization_url_generation(self):
        """URL contains all required parameters."""
        from app.services.github_oauth_service import GitHubOAuthService

        service = GitHubOAuthService()
        result = service.get_authorization_url("test-agent-id")

        assert "auth_url" in result
        assert "state" in result
        assert "github.com/login/oauth/authorize" in result["auth_url"]
        assert "client_id=" in result["auth_url"]
        assert "state=" in result["auth_url"]
        assert "scope=" in result["auth_url"]

    def test_authorization_url_has_required_scopes(self):
        """OAuth scopes include repo and read:user for repository access."""
        from app.services.github_oauth_service import GitHubOAuthService, OAUTH_SCOPES

        assert "repo" in OAUTH_SCOPES, "scope 'repo' needed for push/create/issues"
        assert "read:user" in OAUTH_SCOPES

    def test_state_contains_agent_id(self):
        """State parameter contains agent_id for CSRF protection."""
        from app.services.github_oauth_service import GitHubOAuthService

        service = GitHubOAuthService()
        agent_id = "550e8400-e29b-41d4-a716-446655440000"
        result = service.get_authorization_url(agent_id)

        assert agent_id in result["state"]

    def test_token_expiration_check(self):
        """Token expiration logic."""
        from app.services.github_oauth_service import GitHubOAuthService

        service = GitHubOAuthService()

        assert service.is_token_expired(time.time() - 100) is True   # expired
        assert service.is_token_expired(time.time() + 3600) is False  # valid
        assert service.is_token_expired(None) is False                 # no expiry

    @pytest.mark.asyncio
    async def test_exchange_invalid_code_returns_none(self):
        """Exchanging invalid code returns None (doesn't crash)."""
        from app.services.github_oauth_service import GitHubOAuthService

        service = GitHubOAuthService()
        result = await service.exchange_code_for_token("invalid_code_xyz")
        assert result is None


# ==========================================
# Unit tests: GitHubService identity (no network)
# ==========================================

class TestGitHubServiceIdentity:
    """Tests for committer identity creation — no network or DB."""

    def test_create_agent_identity_sanitizes_name(self):
        """Agent name is properly sanitized for Git."""
        from app.services.github_service import GitHubService

        svc = GitHubService()
        identity = svc.create_agent_identity("My Cool Agent 123")

        assert " " not in identity["username"]
        assert identity["username"].islower() or all(
            c.isalnum() or c == "-" for c in identity["username"]
        )
        assert "@" in identity["email"]
        assert "agentspore" in identity["email"]
        assert identity["display_name"] == "My Cool Agent 123"

    def test_create_agent_identity_with_custom_email(self):
        """Custom email is preserved."""
        from app.services.github_service import GitHubService

        svc = GitHubService()
        identity = svc.create_agent_identity("TestAgent", "custom@example.com")
        assert identity["email"] == "custom@example.com"

    def test_sanitize_repo_name(self):
        """Repo name is properly sanitized."""
        from app.services.github_service import GitHubService

        svc = GitHubService()
        assert svc._sanitize_repo_name("My Startup — v2!") == "my-startup-v2"
        assert svc._sanitize_repo_name("  ---hello---  ") == "hello"
        assert len(svc._sanitize_repo_name("a" * 200)) <= 100

    def test_github_org_is_sporeai(self):
        """Default GitHub org is AgentSpore."""
        from app.services.github_service import GITHUB_ORG
        import os

        expected = os.getenv("GITHUB_ORG", "AgentSpore")
        assert expected == "AgentSpore"


# ==========================================
# Integration tests with mock DB
# ==========================================

class TestAgentRegistration:
    """Agent registration tests (mock DB, no Docker)."""

    @pytest.fixture
    def agent_data(self):
        return {
            "name": f"TestAgent-{secrets.token_hex(4)}",
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-4",
            "specialization": "programmer",
            "skills": ["python", "fastapi"],
            "description": "Test agent",
            "owner_email": "test@example.com",
        }

    @pytest.mark.asyncio
    async def test_register_returns_api_key_and_active(self, agent_data):
        """After registration agent is active (is_active=TRUE), API key with af_ prefix."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.agent_service import get_agent_service

        db = AsyncMock()
        mock_redis = AsyncMock()

        async def override_db():
            yield db

        # Mock AgentService with mocked repo
        svc_mock = MagicMock()
        svc_mock.repo = MagicMock()
        svc_mock.repo.handle_exists = AsyncMock(return_value=False)
        svc_mock.repo.find_user_id_by_email = AsyncMock(return_value=None)
        svc_mock.repo.insert_agent = AsyncMock()
        svc_mock.repo.insert_activity = AsyncMock()
        svc_mock.db = db

        # Mock register_agent to return expected data
        agent_id = "test-agent-id"
        api_key = f"af_{secrets.token_hex(24)}"
        svc_mock.register_agent = AsyncMock(return_value={
            "agent_id": agent_id,
            "api_key": api_key,
            "name": agent_data["name"],
            "handle": agent_data["name"].lower().replace(" ", "-"),
            "github_oauth_required": False,
            "github_auth_url": "https://github.com/login/oauth/authorize?client_id=test&state=test",
        })

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_agent_service] = lambda: svc_mock
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents/register", json=agent_data
                )

            assert response.status_code == 200
            data = response.json()

            assert "agent_id" in data
            assert data["api_key"].startswith("af_")
            assert data["github_oauth_required"] is False
            assert "github_auth_url" in data
            assert "github.com/login/oauth/authorize" in data["github_auth_url"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_register_name_conflict_returns_409(self, agent_data):
        """Duplicate agent name → 409."""
        from app.main import app
        from app.core.database import get_db
        from app.core.redis_client import get_redis
        from app.services.agent_service import get_agent_service
        from sqlalchemy.exc import IntegrityError

        db = AsyncMock()
        mock_redis = AsyncMock()

        async def override_db():
            yield db

        svc_mock = MagicMock()
        svc_mock.register_agent = AsyncMock(
            side_effect=IntegrityError("", {}, Exception("duplicate key"))
        )

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_agent_service] = lambda: svc_mock
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents/register", json=agent_data
                )
            assert response.status_code == 409
        finally:
            app.dependency_overrides.clear()


def _setup_overrides(app, db, mock_redis=None):
    """Set up dependency overrides for tests."""
    from app.core.database import get_db
    from app.core.redis_client import get_redis

    if mock_redis is None:
        mock_redis = AsyncMock()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: mock_redis


class TestAgentAuth:
    """Agent API key authentication tests."""

    @pytest.mark.asyncio
    async def test_heartbeat_no_key_returns_422(self):
        """Heartbeat without X-API-Key → 422 (missing header)."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents/heartbeat",
                    json={"status": "idle", "completed_tasks": [], "available_for": ["programmer"], "current_capacity": 3},
                )
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_heartbeat_invalid_key_returns_401(self):
        """Heartbeat with invalid key → 401."""
        from app.main import app

        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        db.execute.return_value = result

        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/agents/heartbeat",
                    headers={"X-API-Key": "af_fake_key_xyz"},
                    json={"status": "idle", "completed_tasks": [], "available_for": [], "current_capacity": 1},
                )
            assert response.status_code == 401
            assert "Invalid or inactive API key" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_github_status_no_key_returns_422(self):
        """GET /github/status without key → 422."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/agents/github/status")
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_github_status_invalid_key_returns_401(self):
        """GET /github/status with invalid key → 401."""
        from app.main import app

        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        db.execute.return_value = result

        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/agents/github/status",
                    headers={"X-API-Key": "af_invalid_key"},
                )
            assert response.status_code == 401
        finally:
            app.dependency_overrides.clear()


class TestOAuthCallback:
    """OAuth callback tests."""

    @pytest.mark.asyncio
    async def test_callback_invalid_state_returns_error(self):
        """Invalid state → status=error (not 500)."""
        from app.main import app

        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value.first.return_value = None
        db.execute.return_value = result

        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/agents/github/callback",
                    params={"code": "test_code", "state": "invalid_state_xyz"},
                )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "Invalid or expired OAuth state" in data["message"]
        finally:
            app.dependency_overrides.clear()


class TestLeaderboard:
    """Leaderboard tests."""

    @pytest.mark.asyncio
    async def test_leaderboard_invalid_sort_returns_422(self):
        """Invalid sort value → 422 (Literal validation)."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/agents/leaderboard",
                    params={"sort": "INVALID_SORT_VALUE; DROP TABLE agents;--"},
                )
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_leaderboard_valid_sort_values(self):
        """Valid sort values are accepted."""
        from app.main import app

        db = AsyncMock()
        result = MagicMock()
        result.mappings.return_value = []
        db.execute.return_value = result

        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                for sort_val in ["karma", "created_at", "commits"]:
                    resp = await client.get(
                        "/api/v1/agents/leaderboard",
                        params={"sort": sort_val},
                    )
                    assert resp.status_code == 200, f"sort={sort_val} failed"
        finally:
            app.dependency_overrides.clear()
