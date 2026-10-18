"""Removed hosted routes and retained external-agent delivery contracts.

HTTP requests use the real application router without starting its lifespan.
Delivery fixtures are synthetic; only outward Redis and webhook calls are
replaced. PostgreSQL outbox coverage stays in test_agent_events.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.background import ALL_TASKS
from app.main import app
from app.services import connection_manager as cm

REMOVED_ROUTES = [
    ("GET", "/api/v1/hosted-agents/running"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/idle-stopped"),
    ("GET", "/api/v1/hosted-agents/models"),
    ("GET", "/api/v1/hosted-agents/forkable"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/fork"),
    ("POST", "/api/v1/hosted-agents/fork-by-agent/00000000-0000-0000-0000-000000000001"),
    ("GET", "/api/v1/hosted-agents/self"),
    ("PATCH", "/api/v1/hosted-agents/self"),
    ("POST", "/api/v1/hosted-agents"),
    ("GET", "/api/v1/hosted-agents"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001"),
    ("PATCH", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001"),
    ("DELETE", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/force-restart"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/chat"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/chat/stream"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/chat"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/checkpoints"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/rewind"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/chat/clear"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/todos"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/diff"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/files"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/files/download"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/files/batch"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/files/agent.md"),
    ("PUT", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/files"),
    ("DELETE", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/files/agent.md"),
    ("GET", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/cron"),
    ("POST", "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/cron"),
    (
        "PATCH",
        "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/cron/00000000-0000-0000-0000-000000000001",
    ),
    (
        "DELETE",
        "/api/v1/hosted-agents/00000000-0000-0000-0000-000000000001/cron/00000000-0000-0000-0000-000000000001",
    ),
    ("GET", "/api/v1/admin/hosted-agents/disk-usage"),
    ("GET", "/api/v1/councils"),
    ("POST", "/api/v1/councils"),
    ("GET", "/api/v1/councils/models"),
    ("GET", "/api/v1/councils/agents"),
    ("GET", "/api/v1/councils/00000000-0000-0000-0000-000000000001"),
    ("GET", "/api/v1/councils/00000000-0000-0000-0000-000000000001/stream"),
    ("POST", "/api/v1/councils/00000000-0000-0000-0000-000000000001/chat"),
    ("GET", "/api/v1/internal/replay-cases"),
    ("POST", "/api/v1/internal/replay-cases"),
    ("GET", "/api/v1/internal/replay-cases/search"),
    ("GET", "/api/v1/execution-log"),
    ("GET", "/api/v1/execution-log/00000000-0000-0000-0000-000000000001"),
    ("GET", "/api/v1/analytics/overview"),
    ("GET", "/api/v1/analytics/activity"),
    ("GET", "/api/v1/analytics/top-agents"),
    ("GET", "/api/v1/analytics/top-projects"),
    ("GET", "/api/v1/analytics/languages"),
    ("GET", "/api/v1/flows"),
    ("POST", "/api/v1/flows"),
    ("GET", "/api/v1/flows/00000000-0000-0000-0000-000000000001"),
    ("POST", "/api/v1/flows/00000000-0000-0000-0000-000000000001/start"),
    ("GET", "/api/v1/flows/agent/my-steps"),
    ("POST", "/api/v1/flows/agent/step/00000000-0000-0000-0000-000000000001/complete"),
    ("GET", "/api/v1/mixer"),
    ("POST", "/api/v1/mixer"),
    ("GET", "/api/v1/mixer/00000000-0000-0000-0000-000000000001"),
    ("POST", "/api/v1/mixer/00000000-0000-0000-0000-000000000001/assemble"),
    ("GET", "/api/v1/mixer/agent/my-chunks"),
    ("POST", "/api/v1/mixer/agent/chunk/00000000-0000-0000-0000-000000000001/complete"),
    ("GET", "/api/v1/hackathons"),
    ("POST", "/api/v1/hackathons"),
    ("GET", "/api/v1/hackathons/current"),
    ("GET", "/api/v1/hackathons/00000000-0000-0000-0000-000000000001"),
    ("PATCH", "/api/v1/hackathons/00000000-0000-0000-0000-000000000001"),
    ("POST", "/api/v1/hackathons/00000000-0000-0000-0000-000000000001/register-project"),
    ("GET", "/api/v1/rentals"),
    ("POST", "/api/v1/rentals"),
    ("GET", "/api/v1/rentals/00000000-0000-0000-0000-000000000001"),
    ("GET", "/api/v1/rentals/00000000-0000-0000-0000-000000000001/messages"),
    ("POST", "/api/v1/rentals/00000000-0000-0000-0000-000000000001/complete"),
    ("GET", "/api/v1/rentals/agent/my-rentals"),
    ("POST", "/api/v1/rentals/agent/rental/00000000-0000-0000-0000-000000000001/submit"),
]


@pytest.mark.parametrize(("method", "path"), REMOVED_ROUTES)
async def test_removed_routes_do_not_accept_requests(method: str, path: str) -> None:
    """Old lifecycle routes are unavailable without authentication or a database."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json={})
    assert response.status_code == 404


def test_openapi_keeps_external_agents_and_battles() -> None:
    """The actual router exposes external agents and battles, never hosted routes."""
    paths = app.openapi()["paths"]
    assert "/api/v1/agents/register" in paths
    assert "/api/v1/agents/heartbeat" in paths
    assert "/api/v1/battles" in paths
    assert not any(
        "/hosted-agents" in path or "/internal/replay-cases" in path or "/councils" in path
        or "/hackathons" in path
        or "/mixer" in path
        or "/rentals" in path
        or "/flows" in path
        for path in paths
    )


def test_scheduler_keeps_battles_without_hosted_tasks() -> None:
    """The real registration tuple excludes permanent-hosted work only."""
    assert {task.name for task in ALL_TASKS} == {
        "governance_expire",
        "github_sync",
        "usage_purge",
        "battle_run",
        "battle_matchmaker",
        "battle_harvester",
    }


@pytest.fixture
def connection_manager(monkeypatch):
    """Isolate the actual manager without starting Redis listeners or sockets."""
    manager = cm.ConnectionManager()
    monkeypatch.setattr(cm, "_manager", manager)
    return manager


async def test_external_event_uses_connected_socket(connection_manager) -> None:
    """Connected external agents still receive frames through the real manager."""
    socket = AsyncMock()
    connection_manager._connections["external"] = socket
    event = {"type": "dm", "content": "hello"}
    with patch.object(cm.AgentWebhookService, "deliver", new_callable=AsyncMock) as webhook:
        result = await cm.deliver_event("external", event)
    assert result == cm.DeliveryResult.DELIVERED
    socket.send_json.assert_awaited_once_with(event)
    webhook.assert_not_awaited()


@pytest.mark.parametrize("subscribers", [0, 1])
async def test_external_event_redis_then_webhook(connection_manager, subscribers: int) -> None:
    """A Redis publish with no receiver falls through to the outward webhook."""
    redis = AsyncMock()
    redis.publish.return_value = subscribers
    event = {"type": "task", "task_id": "synthetic"}
    with (
        patch.object(cm, "get_redis", AsyncMock(return_value=redis)),
        patch.object(cm.AgentWebhookService, "deliver", AsyncMock(return_value=True)) as webhook,
    ):
        result = await cm.deliver_event("external", event)
    assert result == cm.DeliveryResult.DELIVERED
    redis.publish.assert_awaited_once()
    assert webhook.await_count == (0 if subscribers else 1)


@pytest.mark.parametrize("event_type", ["dm", "task", "mention", "notification", "ping"])
async def test_unreachable_external_agent_never_launches_runtime(
    connection_manager,
    event_type: str,
) -> None:
    """No delivery path schedules a sandbox when no live transport receives an event."""
    redis = AsyncMock()
    redis.publish.return_value = 0
    with (
        patch.object(cm, "get_redis", AsyncMock(return_value=redis)),
        patch.object(cm.AgentWebhookService, "deliver", AsyncMock(return_value=False)),
        patch.object(cm.asyncio, "create_task", side_effect=AssertionError("unexpected task")),
    ):
        result = await cm.deliver_event("external", {"type": event_type})
    assert result == cm.DeliveryResult.FAILED
