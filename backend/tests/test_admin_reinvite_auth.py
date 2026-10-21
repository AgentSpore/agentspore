"""Reinvitation requires human administrator permission before any side effect."""

from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.v1.agents import router
from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token
from app.models import User
from app.services.agent_service import AgentService, get_agent_service

ENDPOINT = "/api/v1/agents/admin/reinvite-github-users"
SUBJECT = "00000000-0000-4000-8000-000000000001"


@pytest.fixture
def invitation_service() -> AsyncMock:
    """Replace the external invitation boundary with a tracked sentinel."""
    service = AsyncMock(spec=AgentService)
    service.reinvite_github_users.return_value = {"sentinel": "admin-only"}
    return service


@pytest.fixture
def reinvite_app(invitation_service: AsyncMock) -> FastAPI:
    """Mount the production router with real authentication and permission guards."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def unused_database() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_db] = unused_database
    app.dependency_overrides[get_agent_service] = lambda: invitation_service
    return app


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "synthetic-agent-key"}])
async def test_missing_human_auth_never_invites(
    reinvite_app: FastAPI, invitation_service: AsyncMock, headers: dict[str, str]
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=reinvite_app), base_url="http://test"
    ) as client:
        response = await client.post(ENDPOINT, headers=headers)
    invitation_service.reinvite_github_users.assert_not_awaited()
    assert response.status_code == 401


@pytest.mark.parametrize("kind", ["invalid", "expired", "refresh"])
async def test_invalid_human_auth_never_invites(
    reinvite_app: FastAPI, invitation_service: AsyncMock, kind: str
) -> None:
    tokens = {
        "invalid": "not-a-jwt",
        "expired": create_access_token(SUBJECT, timedelta(seconds=-60)),
        "refresh": create_refresh_token(SUBJECT),
    }
    async with AsyncClient(
        transport=ASGITransport(app=reinvite_app), base_url="http://test"
    ) as client:
        response = await client.post(ENDPOINT, headers={"Authorization": "Bearer " + tokens[kind]})
    invitation_service.reinvite_github_users.assert_not_awaited()
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"


@pytest.mark.parametrize("is_admin", [False, True])
async def test_human_admin_permission_controls_invitation(
    reinvite_app: FastAPI, invitation_service: AsyncMock, is_admin: bool
) -> None:
    user = User(id=UUID(SUBJECT), email="synthetic@example.invalid", name="Test", is_admin=is_admin)
    reinvite_app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=reinvite_app), base_url="http://test"
    ) as client:
        response = await client.post(ENDPOINT)
    if is_admin:
        invitation_service.reinvite_github_users.assert_awaited_once_with()
        assert response.status_code == 200
        assert response.json() == {"sentinel": "admin-only"}
    else:
        invitation_service.reinvite_github_users.assert_not_awaited()
        assert response.status_code == 403
        assert response.json()["detail"] == "Admin access required"
