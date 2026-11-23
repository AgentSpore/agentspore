"""The suite-wide offline guard turns any real network send into a loud failure."""

import asyncio

import httpx
import pytest

REAL_URL = "https://agentspore.com/api/v1/agents/register"


def test_sync_client_to_real_host_is_refused():
    with httpx.Client() as client:
        with pytest.raises(RuntimeError, match="agentspore.com"):
            client.post(REAL_URL, json={})


def test_async_client_to_real_host_is_refused():
    async def send():
        async with httpx.AsyncClient() as client:
            await client.post(REAL_URL, json={})

    with pytest.raises(RuntimeError, match="agentspore.com"):
        asyncio.run(send())


def test_mock_transport_still_works():
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    with httpx.Client(transport=transport) as client:
        assert client.get(REAL_URL).status_code == 204
