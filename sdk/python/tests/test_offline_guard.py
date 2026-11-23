"""The suite-wide offline guard turns any real network send into a loud failure."""

import asyncio

import httpx
import pytest

REAL_URL = "https://agentspore.invalid/api/v1/agents/register"


@pytest.fixture(scope="module")
def module_scoped_response():
    """Runs before any function-scoped fixture; must already hit the guard."""
    with httpx.Client() as client, pytest.raises(RuntimeError) as caught:
        client.get(REAL_URL)
    return caught.value


def test_module_scoped_fixture_hits_guard(module_scoped_response):
    assert "agentspore.invalid" in str(module_scoped_response)


def test_sync_client_to_real_host_is_refused():
    with httpx.Client() as client, pytest.raises(RuntimeError, match="agentspore.invalid"):
        client.post(REAL_URL, json={})


def test_async_client_to_real_host_is_refused():
    async def send():
        async with httpx.AsyncClient() as client:
            await client.post(REAL_URL, json={})

    with pytest.raises(RuntimeError, match="agentspore.invalid"):
        asyncio.run(send())


def test_mock_transport_still_works():
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    with httpx.Client(transport=transport) as client:
        assert client.get(REAL_URL).status_code == 204
