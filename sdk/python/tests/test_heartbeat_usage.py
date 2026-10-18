"""Typed heartbeat clients preserve service metrics from real HTTP response parsing."""

from dataclasses import asdict

import httpx
import pytest
from agentspore import AgentSpore, AsyncAgentSpore

USAGE = {
    "project_id": "12345678-1234-4234-8234-123456789012",
    "uniques_1d": 3,
    "uniques_7d": 11,
    "retention_d7": 0.25,
}


@pytest.fixture
def heartbeat_transport(with_usage):
    payload = {"tasks": [], "notifications": [], "direct_messages": [], "feedback": []}
    if with_usage:
        payload["project_usage"] = [USAGE]

    def respond(request):
        assert request.method == "POST"
        assert request.url.path == "/api/v1/agents/heartbeat"
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(respond)


@pytest.mark.parametrize("with_usage", [True, False], ids=["usage", "legacy"])
def test_sync_heartbeat_preserves_typed_usage(heartbeat_transport, with_usage):
    with AgentSpore("synthetic", base_url="https://platform.invalid") as client:
        client._client.close()
        client._client = httpx.Client(
            base_url=client.base_url,
            headers={"X-API-Key": "synthetic"},
            transport=heartbeat_transport,
        )
        response = client.heartbeat()
    assert [asdict(usage) for usage in response.project_usage] == ([USAGE] if with_usage else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("with_usage", [True, False], ids=["usage", "legacy"])
async def test_async_heartbeat_preserves_typed_usage(heartbeat_transport, with_usage):
    async with AsyncAgentSpore("synthetic", base_url="https://platform.invalid") as client:
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url=client.base_url,
            headers={"X-API-Key": "synthetic"},
            transport=heartbeat_transport,
        )
        response = await client.heartbeat()
    assert [asdict(usage) for usage in response.project_usage] == ([USAGE] if with_usage else [])
