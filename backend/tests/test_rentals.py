"""Tests for Agent Rental feature."""
import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


def _setup_overrides(app, db, mock_redis=None):
    from app.core.database import get_db
    from app.core.redis_client import get_redis

    if mock_redis is None:
        mock_redis = AsyncMock()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: mock_redis


def _override_rental_deps(app, repo_mock=None, agent_repo_mock=None, payout_mock=None):
    """Override rental-related DI dependencies."""
    from app.repositories.rental_repo import get_rental_repo
    from app.repositories.agent_repo import get_agent_repo
    from app.services.payout_service import get_payout_service

    if repo_mock:
        app.dependency_overrides[get_rental_repo] = lambda: repo_mock
    if agent_repo_mock:
        app.dependency_overrides[get_agent_repo] = lambda: agent_repo_mock
    if payout_mock:
        app.dependency_overrides[get_payout_service] = lambda: payout_mock


def _mock_user(user_id=None, name="TestUser", email="test@example.com"):
    """Create a mock User object."""
    from app.models import User
    user = MagicMock(spec=User)
    user.id = user_id or uuid.uuid4()
    user.name = name
    user.email = email
    user.is_admin = False
    return user


def _override_current_user(app, user):
    """Override CurrentUser dependency."""
    from app.api.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user


# ==========================================
# Schema tests
# ==========================================

class TestRentalSchemas:
    """Validation tests for rental Pydantic schemas."""

    def test_create_rental_valid(self):
        from app.schemas.rentals import CreateRentalRequest
        req = CreateRentalRequest(agent_id="some-uuid", title="Build me a website")
        assert req.agent_id == "some-uuid"
        assert req.title == "Build me a website"

    def test_create_rental_empty_title_rejected(self):
        from app.schemas.rentals import CreateRentalRequest
        with pytest.raises(Exception):
            CreateRentalRequest(agent_id="some-uuid", title="")

    def test_create_rental_title_too_long_rejected(self):
        from app.schemas.rentals import CreateRentalRequest
        with pytest.raises(Exception):
            CreateRentalRequest(agent_id="some-uuid", title="x" * 301)

    def test_complete_rental_valid(self):
        from app.schemas.rentals import CompleteRentalRequest
        req = CompleteRentalRequest(rating=5, review="Great work!")
        assert req.rating == 5
        assert req.review == "Great work!"

    def test_complete_rental_rating_out_of_range(self):
        from app.schemas.rentals import CompleteRentalRequest
        with pytest.raises(Exception):
            CompleteRentalRequest(rating=0)
        with pytest.raises(Exception):
            CompleteRentalRequest(rating=6)

    def test_complete_rental_no_review_ok(self):
        from app.schemas.rentals import CompleteRentalRequest
        req = CompleteRentalRequest(rating=3)
        assert req.review is None

    def test_cancel_rental_optional_reason(self):
        from app.schemas.rentals import CancelRentalRequest
        req = CancelRentalRequest()
        assert req.reason is None
        req2 = CancelRentalRequest(reason="Agent went offline")
        assert req2.reason == "Agent went offline"

    def test_message_request_valid(self):
        from app.schemas.rentals import RentalMessageRequest
        req = RentalMessageRequest(content="Hello agent!")
        assert req.message_type == "text"
        assert req.file_url is None

    def test_message_request_file(self):
        from app.schemas.rentals import RentalMessageRequest
        req = RentalMessageRequest(
            content="Here is the design",
            message_type="file",
            file_url="https://example.com/design.png",
            file_name="design.png",
        )
        assert req.message_type == "file"
        assert req.file_name == "design.png"

    def test_message_request_empty_content_rejected(self):
        from app.schemas.rentals import RentalMessageRequest
        with pytest.raises(Exception):
            RentalMessageRequest(content="")

    def test_message_request_too_long_rejected(self):
        from app.schemas.rentals import RentalMessageRequest
        with pytest.raises(Exception):
            RentalMessageRequest(content="x" * 5001)


# ==========================================
# Config tests
# ==========================================

class TestRentalConfig:
    """Tests for rental feature flags in config."""

    def test_rental_payment_disabled_by_default(self):
        from app.core.config import Settings
        s = Settings(secret_key="test123")
        assert s.rental_payment_enabled is False

    def test_rental_platform_fee_default(self):
        from app.core.config import Settings
        s = Settings(secret_key="test123")
        assert s.rental_platform_fee_pct == 0.01

    def test_rental_payment_can_be_enabled(self):
        from app.core.config import Settings
        s = Settings(secret_key="test123", rental_payment_enabled=True)
        assert s.rental_payment_enabled is True


# ==========================================
# API auth tests
# ==========================================

class TestRentalAPIAuth:
    """Tests for rental API authentication requirements."""

    @pytest.mark.asyncio
    async def test_create_rental_no_auth_returns_401(self):
        """POST /rentals without auth returns 401/403."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/rentals",
                    json={"agent_id": str(uuid.uuid4()), "title": "Do something"},
                )
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_rentals_no_auth_returns_401(self):
        """GET /rentals without auth returns 401/403."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/rentals")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_rental_no_auth_returns_401(self):
        """GET /rentals/:id without auth returns 401/403."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/v1/rentals/{uuid.uuid4()}")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_agent_rentals_no_key_returns_422(self):
        """GET /rentals/agent/my-rentals without X-API-Key returns 422."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/rentals/agent/my-rentals")
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_agent_rentals_invalid_key_returns_401(self):
        """GET /rentals/agent/my-rentals with invalid key returns 401."""
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
                    "/api/v1/rentals/agent/my-rentals",
                    headers={"X-API-Key": "af_invalid_key"},
                )
            assert response.status_code == 401
        finally:
            app.dependency_overrides.clear()


# ==========================================
# API functional tests (with mock user)
# ==========================================

class TestRentalAPIFunctional:
    """Functional tests for rental API with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_create_rental_agent_not_found(self):
        """Create rental for nonexistent agent returns 404."""
        from app.main import app

        db = AsyncMock()
        mock_redis = AsyncMock()
        _setup_overrides(app, db, mock_redis)

        user = _mock_user()
        _override_current_user(app, user)

        agent_repo_mock = MagicMock()
        agent_repo_mock.get_agent_by_id = AsyncMock(return_value=None)

        repo_mock = MagicMock()

        _override_rental_deps(app, repo_mock=repo_mock, agent_repo_mock=agent_repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/rentals",
                    json={"agent_id": str(uuid.uuid4()), "title": "Build a website"},
                    headers={"Authorization": "Bearer fake-token"},
                )
            assert response.status_code == 404
            assert "Agent not found" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_rental_agent_offline(self):
        """Create rental for offline agent returns 400."""
        from app.main import app

        db = AsyncMock()
        mock_redis = AsyncMock()
        _setup_overrides(app, db, mock_redis)

        user = _mock_user()
        _override_current_user(app, user)

        agent_id = str(uuid.uuid4())

        agent_repo_mock = MagicMock()
        agent_repo_mock.get_agent_by_id = AsyncMock(return_value={
            "id": agent_id, "name": "TestAgent", "is_active": False,
        })

        repo_mock = MagicMock()

        _override_rental_deps(app, repo_mock=repo_mock, agent_repo_mock=agent_repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/rentals",
                    json={"agent_id": agent_id, "title": "Build a website"},
                    headers={"Authorization": "Bearer fake-token"},
                )
            assert response.status_code == 400
            assert "offline" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_rental_success(self):
        """Create rental for active agent returns 200 with rental ID."""
        from app.main import app

        db = AsyncMock()
        mock_redis = AsyncMock()
        _setup_overrides(app, db, mock_redis)

        user = _mock_user(name="Alice")
        _override_current_user(app, user)

        agent_id = str(uuid.uuid4())
        rental_id = str(uuid.uuid4())

        agent_repo_mock = MagicMock()
        agent_repo_mock.get_agent_by_id = AsyncMock(return_value={
            "id": agent_id, "name": "CoolAgent", "is_active": True,
        })

        repo_mock = MagicMock()
        repo_mock.create_rental = AsyncMock(return_value={
            "id": rental_id, "status": "active", "created_at": "2026-03-14T10:00:00",
        })
        repo_mock.insert_message = AsyncMock(return_value={
            "id": str(uuid.uuid4()), "created_at": "2026-03-14T10:00:00",
        })

        payout_mock = MagicMock()

        _override_rental_deps(
            app, repo_mock=repo_mock, agent_repo_mock=agent_repo_mock,
            payout_mock=payout_mock,
        )

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/rentals",
                    json={"agent_id": agent_id, "title": "Build a landing page"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == rental_id
            assert data["status"] == "active"
            assert data["price_tokens"] == 0  # payment disabled
            assert data["platform_fee"] == 0
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_rentals_empty(self):
        """List rentals with no rentals returns empty list."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        repo_mock = MagicMock()
        repo_mock.list_user_rentals = AsyncMock(return_value=[])

        _override_rental_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/rentals",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 200
            assert response.json() == []
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_rental_access_denied(self):
        """Get rental owned by another user returns 403."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        rental_id = str(uuid.uuid4())
        other_user_id = uuid.uuid4()

        repo_mock = MagicMock()
        repo_mock.get_rental_by_id = AsyncMock(return_value={
            "id": rental_id, "user_id": other_user_id,
            "agent_id": uuid.uuid4(), "title": "Test",
            "status": "active", "price_tokens": 0, "platform_fee": 0,
            "rating": None, "review": None, "created_at": "2026-03-14",
            "completed_at": None, "cancelled_at": None,
            "agent_name": "X", "agent_handle": "x", "specialization": "programmer",
            "user_name": "Other",
        })

        _override_rental_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    f"/api/v1/rentals/{rental_id}",
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_complete_rental_not_active(self):
        """Complete an already completed rental returns 400."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        rental_id = str(uuid.uuid4())

        repo_mock = MagicMock()
        repo_mock.get_rental_by_id = AsyncMock(return_value={
            "id": rental_id, "user_id": user.id,
            "agent_id": uuid.uuid4(), "title": "Test",
            "status": "completed", "price_tokens": 0, "platform_fee": 0,
            "rating": 5, "review": None, "created_at": "2026-03-14",
            "completed_at": "2026-03-14", "cancelled_at": None,
            "agent_name": "X", "agent_handle": "x", "specialization": "programmer",
            "user_name": "Test", "agent_is_active": True,
        })

        agent_repo_mock = MagicMock()

        _override_rental_deps(app, repo_mock=repo_mock, agent_repo_mock=agent_repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/rentals/{rental_id}/complete",
                    json={"rating": 5},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 400
            assert "not active" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_cancel_rental_success(self):
        """Cancel an active rental returns status cancelled."""
        from app.main import app

        db = AsyncMock()
        _setup_overrides(app, db)

        user = _mock_user()
        _override_current_user(app, user)

        rental_id = str(uuid.uuid4())

        agent_id = uuid.uuid4()
        active_rental = {
            "id": rental_id, "user_id": user.id,
            "agent_id": agent_id, "title": "Test",
            "status": "active", "price_tokens": 0, "platform_fee": 0,
            "rating": None, "review": None, "created_at": "2026-03-14",
            "completed_at": None, "cancelled_at": None,
            "agent_name": "X", "agent_handle": "x", "specialization": "programmer",
            "user_name": "Test", "agent_is_active": True,
        }
        cancelled_rental = {
            **active_rental,
            "status": "cancelled", "cancelled_at": "2026-03-14",
        }

        repo_mock = MagicMock()
        repo_mock.get_rental_by_id = AsyncMock(side_effect=[active_rental, cancelled_rental])
        repo_mock.update_rental_status = AsyncMock()
        repo_mock.insert_message = AsyncMock(return_value={
            "id": str(uuid.uuid4()), "created_at": "2026-03-14",
        })

        payout_mock = MagicMock()
        payout_mock.try_refund_rental = AsyncMock()

        _override_rental_deps(app, repo_mock=repo_mock, payout_mock=payout_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/rentals/{rental_id}/cancel",
                    json={"reason": "Agent went offline"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 200
            assert response.json()["status"] == "cancelled"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_send_message_to_inactive_rental(self):
        """Send message to cancelled rental returns 400."""
        from app.main import app

        db = AsyncMock()
        mock_redis = AsyncMock()
        _setup_overrides(app, db, mock_redis)

        user = _mock_user()
        _override_current_user(app, user)

        rental_id = str(uuid.uuid4())

        repo_mock = MagicMock()
        repo_mock.get_rental_by_id = AsyncMock(return_value={
            "id": rental_id, "user_id": user.id,
            "agent_id": uuid.uuid4(), "title": "Test",
            "status": "cancelled", "price_tokens": 0, "platform_fee": 0,
            "rating": None, "review": None, "created_at": "2026-03-14",
            "completed_at": None, "cancelled_at": "2026-03-14",
            "agent_name": "X", "agent_handle": "x", "specialization": "programmer",
            "user_name": "Test", "agent_is_active": True,
        })

        _override_rental_deps(app, repo_mock=repo_mock)

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/rentals/{rental_id}/messages",
                    json={"content": "Hello?"},
                    headers={"Authorization": "Bearer fake-token"},
                )

            assert response.status_code == 400
            assert "not active" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()


# ==========================================
# Heartbeat integration test
# ==========================================

class TestHeartbeatRentals:
    """Test that rentals field is present in heartbeat response schema."""

    def test_heartbeat_response_has_rentals_field(self):
        """HeartbeatResponseBody schema includes rentals field."""
        from app.schemas.agents import HeartbeatResponseBody
        resp = HeartbeatResponseBody()
        assert hasattr(resp, "rentals")
        assert resp.rentals == []

    def test_heartbeat_response_with_rentals(self):
        """HeartbeatResponseBody can carry rental data."""
        from app.schemas.agents import HeartbeatResponseBody
        resp = HeartbeatResponseBody(rentals=[
            {"rental_id": "abc-123", "user_name": "Alice", "title": "Build website", "created_at": "2026-03-14"},
        ])
        assert len(resp.rentals) == 1
        assert resp.rentals[0]["user_name"] == "Alice"
