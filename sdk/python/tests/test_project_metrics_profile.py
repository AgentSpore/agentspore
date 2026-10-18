"""Existing SDK producers preserve legacy payloads and explicit metrics profiles."""

import json
import unittest

import httpx

from agentspore import AgentSpore, AsyncAgentSpore


def project_response(request):
    """Exercise the HTTP transport boundary without making external requests."""
    payload = json.loads(request.content)
    assert request.method == "POST"
    assert request.url.path == "/api/v1/agents/projects"
    assert request.headers["X-API-Key"] == "synthetic"
    expected = {"title", "description", "category", "tech_stack"}
    if "vcs_provider" in payload:
        expected.add("vcs_provider")
    if payload.get("metrics_profile") is not None:
        expected.add("metrics_profile")
        assert payload["metrics_profile"] == "family_safety"
    assert set(payload) == expected
    result = {"id": "synthetic-project", "title": payload["title"]}
    if "metrics_profile" in payload:
        result["metrics_profile"] = payload["metrics_profile"]
    return httpx.Response(200, json=result)


class SyncProjectProfileTests(unittest.TestCase):
    def test_legacy_and_explicit_profile_cross_real_httpx_boundary(self):
        for profile in (None, "family_safety"):
            with self.subTest(profile=profile):
                with AgentSpore("synthetic", base_url="https://example.invalid") as client:
                    client._client.close()
                    client._client = httpx.Client(
                        base_url="https://example.invalid",
                        headers={"X-API-Key": "synthetic"},
                        transport=httpx.MockTransport(project_response),
                    )
                    kwargs = {} if profile is None else {"metrics_profile": profile}
                    result = client.create_project("Family safety", "Synthetic", **kwargs)
                    self.assertEqual(result.metrics_profile, profile)


class AsyncProjectProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_and_explicit_profile_cross_real_httpx_boundary(self):
        for profile in (None, "family_safety"):
            with self.subTest(profile=profile):
                async with AsyncAgentSpore("synthetic", base_url="https://example.invalid") as client:
                    await client._client.aclose()
                    client._client = httpx.AsyncClient(
                        base_url="https://example.invalid",
                        headers={"X-API-Key": "synthetic"},
                        transport=httpx.MockTransport(project_response),
                    )
                    kwargs = {} if profile is None else {"metrics_profile": profile}
                    result = await client.create_project("Family safety", "Synthetic", **kwargs)
                    self.assertEqual(result.metrics_profile, profile)


if __name__ == "__main__":
    unittest.main()
