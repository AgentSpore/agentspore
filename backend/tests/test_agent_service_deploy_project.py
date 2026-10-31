"""Route-level guard for POST /projects/:id/deploy while A4_DEPLOY_ENABLED is off.

FastAPI wraps a dict-detail HTTPException as {"detail": {...}}, so an agent reading
resp["status"] at the top level would fail; only the route-level path can see that.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport

from app.main import app as fastapi_app
from app.services.agent_service import get_agent_by_api_key
from app.services.deployment_admission import DeploymentAdmission, get_deployment_admission


@pytest.fixture
def deploy_route_client():
    admission = AsyncMock(spec=DeploymentAdmission)
    admission.submit.side_effect = HTTPException(
        status_code=503,
        detail={"status": "unavailable", "reason_code": "deploy_disabled"},
    )
    fastapi_app.dependency_overrides[get_agent_by_api_key] = lambda: {"id": str(uuid4())}
    fastapi_app.dependency_overrides[get_deployment_admission] = lambda: admission
    client = httpx.AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")
    yield client
    fastapi_app.dependency_overrides.pop(get_agent_by_api_key, None)
    fastapi_app.dependency_overrides.pop(get_deployment_admission, None)


@pytest.mark.asyncio
async def test_deploy_route_returns_503_with_nested_status(deploy_route_client) -> None:
    async with deploy_route_client as client:
        resp = await client.post(
            f"/api/v1/agents/projects/{uuid4()}/deploy",
            json={"commit_sha": "a" * 40},
            headers={"Idempotency-Key": str(uuid4())},
        )

    assert resp.status_code == 503
    body = resp.json()
    assert "status" not in body
    assert body["detail"]["reason_code"] == "deploy_disabled"
