"""Both legacy Python producers carry the binder profile across real HTTPX transport."""

import json
import unittest

import httpx

from agentspore import AgentSpore, AsyncAgentSpore


def response(request):
    """Verify the outbound wire payload and echo the new profile from the API boundary."""
    payload = json.loads(request.content)
    assert request.method == "POST" and request.url.path == "/api/v1/agents/projects"
    assert request.headers["X-API-Key"] == "synthetic"
    assert payload["metrics_profile"] == "emergency_binder"
    expected = {"title", "description", "category", "tech_stack", "metrics_profile"}
    if "vcs_provider" in payload:
        expected.add("vcs_provider")
    assert set(payload) == expected
    return httpx.Response(200, json={"id": "synthetic-project", **payload})


class BinderSyncTests(unittest.TestCase):
    def test_sync_create_preserves_binder_profile(self):
        with AgentSpore("synthetic", base_url="https://example.invalid") as client:
            client._client.close()
            client._client = httpx.Client(
                base_url="https://example.invalid",
                headers={"X-API-Key": "synthetic"},
                transport=httpx.MockTransport(response),
            )
            project = client.create_project(
                "Binder", "Synthetic", metrics_profile="emergency_binder"
            )
            self.assertEqual(project.metrics_profile, "emergency_binder")


class BinderAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_create_preserves_binder_profile(self):
        async with AsyncAgentSpore(
            "synthetic", base_url="https://example.invalid"
        ) as client:
            await client._client.aclose()
            client._client = httpx.AsyncClient(
                base_url="https://example.invalid",
                headers={"X-API-Key": "synthetic"},
                transport=httpx.MockTransport(response),
            )
            project = await client.create_project(
                "Binder", "Synthetic", metrics_profile="emergency_binder"
            )
            self.assertEqual(project.metrics_profile, "emergency_binder")
