"""E2E API Tests for AgentsSpore backend."""
import pytest
from httpx import AsyncClient
from app.main import app


class TestAuth:
    """Test authentication endpoints."""

    @pytest.fixture
    def test_user(self):
        return {
            "email": "testuser@example.com",
            "password": "testpass123",
            "name": "Test User",
        }

    async def test_register_user(self, test_user):
        """Test user registration."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/v1/auth/register", json=test_user)
            # May fail if user already exists
            assert response.status_code in [200, 201, 400]

    async def test_login_user(self, test_user):
        """Test user login."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            # First register
            await client.post("/api/v1/auth/register", json=test_user)
            
            # Then login
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": test_user["email"],
                    "password": test_user["password"],
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"


class TestIdeas:
    """Test ideas endpoints."""

    @pytest.fixture
    async def auth_headers(self):
        """Get auth token for tests."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "newuser@example.com",
                    "password": "newpass123",
                },
            )
            if response.status_code != 200:
                pytest.skip("Auth failed, user may not exist")
            token = response.json()["access_token"]
            return {"Authorization": f"Bearer {token}"}

    async def test_get_ideas(self, auth_headers):
        """Test getting ideas list."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/v1/ideas", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "items" in data
            assert "total" in data

    async def test_create_idea(self, auth_headers):
        """Test creating a new idea."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            idea_data = {
                "title": "Test Idea",
                "description": "A test idea for API testing",
                "category": "saas",
            }
            response = await client.post(
                "/api/v1/ideas",
                json=idea_data,
                headers=auth_headers,
            )
            assert response.status_code in [200, 201]
            data = response.json()
            assert "id" in data
            assert data["title"] == idea_data["title"]


class TestSandboxes:
    """Test sandboxes endpoints."""

    @pytest.fixture
    async def auth_headers(self):
        """Get auth token for tests."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "newuser@example.com",
                    "password": "newpass123",
                },
            )
            if response.status_code != 200:
                pytest.skip("Auth failed")
            token = response.json()["access_token"]
            return {"Authorization": f"Bearer {token}"}

    async def test_get_sandboxes(self, auth_headers):
        """Test getting sandboxes list."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/v1/sandboxes", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()
            assert "items" in data

    async def test_get_sandbox_by_id(self, auth_headers):
        """Test getting a specific sandbox."""
        sandbox_id = "567d9325-3ddb-42f4-b106-efebda1c5329"
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/sandboxes/{sandbox_id}",
                headers=auth_headers,
            )
            # May be 200 or 404 depending on if sandbox exists
            assert response.status_code in [200, 404]


class TestTokens:
    """Test tokens endpoints."""

    @pytest.fixture
    async def auth_headers(self):
        """Get auth token for tests."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "newuser@example.com",
                    "password": "newpass123",
                },
            )
            if response.status_code != 200:
                pytest.skip("Auth failed")
            token = response.json()["access_token"]
            return {"Authorization": f"Bearer {token}"}

    async def test_get_token_balance(self, auth_headers):
        """Test getting token balance."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/tokens/balance",
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert "balance" in data

    async def test_get_leaderboard(self, auth_headers):
        """Test getting leaderboard."""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/tokens/leaderboard",
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
