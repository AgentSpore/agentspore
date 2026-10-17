"""create_project exposes the backend `warning` (e.g. `vcs_unavailable:`) to SDK callers."""

import unittest

import httpx

from agentspore import AgentSpore, AsyncAgentSpore

WARNING = "vcs_unavailable: repository was not created"


def handler_with(extra):
    def handler(request):
        return httpx.Response(200, json={"id": "synthetic-project", "title": "T", "repo_url": None, **extra})

    return handler


class SyncProjectWarningTests(unittest.TestCase):
    def create(self, extra):
        with AgentSpore("synthetic", base_url="https://example.invalid") as client:
            client._client.close()
            client._client = httpx.Client(
                base_url="https://example.invalid", transport=httpx.MockTransport(handler_with(extra))
            )
            return client.create_project("T", "Synthetic")

    def test_warning_is_exposed(self):
        project = self.create({"warning": WARNING})
        self.assertEqual(project.warning, WARNING)
        self.assertIsNone(project.repo_url)

    def test_missing_warning_is_none(self):
        self.assertIsNone(self.create({}).warning)


class AsyncProjectWarningTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, extra):
        async with AsyncAgentSpore("synthetic", base_url="https://example.invalid") as client:
            await client._client.aclose()
            client._client = httpx.AsyncClient(
                base_url="https://example.invalid", transport=httpx.MockTransport(handler_with(extra))
            )
            return await client.create_project("T", "Synthetic")

    async def test_warning_is_exposed(self):
        project = await self.create({"warning": WARNING})
        self.assertEqual(project.warning, WARNING)
        self.assertIsNone(project.repo_url)

    async def test_missing_warning_is_none(self):
        self.assertIsNone((await self.create({})).warning)


if __name__ == "__main__":
    unittest.main()
