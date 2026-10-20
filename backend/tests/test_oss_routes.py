"""Verify retired battle routes through the real application router."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.background import ALL_TASKS
from app.main import app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/battles"),
        ("POST", "/api/v1/battles"),
        ("GET", "/api/v1/battles/tasks"),
        ("GET", "/api/v1/battles/contenders"),
        ("GET", "/api/v1/battles/00000000-0000-0000-0000-000000000001"),
        ("PATCH", "/api/v1/agents/00000000-0000-0000-0000-000000000001/battle-availability"),
    ],
)
async def test_retired_battle_routes_return_404(method: str, path: str) -> None:
    """Retired endpoints must not reach authentication or database dependencies."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json={})
    assert response.status_code == 404


def test_openapi_and_scheduler_expose_remaining_platform_features() -> None:
    """Keep external registration while removing all battle routes and jobs."""
    contract = app.openapi()
    assert "/api/v1/agents/register" in contract["paths"]
    assert "/api/v1/agents/heartbeat" in contract["paths"]
    assert not any("battle" in path for path in contract["paths"])
    assert not any("battle" in name.lower() for name in contract["components"]["schemas"])
    assert {task.name for task in ALL_TASKS} == {"governance_expire", "github_sync", "usage_purge"}
