"""Exercise scenario guards through the real SDK and an HTTP boundary."""

import httpx
import pytest

from agentspore_sdk.marketplace import MarketplaceClient
from examples.review_cycle import run_review_cycle


@pytest.mark.parametrize("failure", ["timeout", "malformed", "wrong_budget"])
async def test_initial_failure_stops_without_creating_tasks(failure):
    requests = []

    def respond(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic unknown outcome", request=request)
        return httpx.Response(
            200,
            json={}
            if failure == "malformed"
            else {
                "available": 99,
                "reserved": 0,
                "initial_grant_applied": True,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as http:
        client = MarketplaceClient(http)
        with pytest.raises((httpx.ReadTimeout, ValueError)):
            await run_review_cycle(client, client, "project", client)
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "malformed", "foreign"])
async def test_submission_failure_never_reviews_or_creates_another_order(failure):
    requests = []
    responses = [
        {"available": 100, "reserved": 0, "initial_grant_applied": True},
        {"available": 0, "reserved": 0, "initial_grant_applied": False},
        {
            "task_id": "12345678-1234-1234-1234-123456789012",
            "status": "open",
            "created": True,
        },
        {"status": "claimed"},
        {
            "version": 1,
            "relationship": "external_unverified"
            if failure == "foreign"
            else "internal",
        },
        {},
    ]

    def respond(request):
        requests.append(request)
        if len(requests) == 6 and failure == "timeout":
            raise httpx.ReadTimeout("Synthetic unknown submission", request=request)
        return httpx.Response(200, json=responses[len(requests) - 1])

    async with httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(respond)
    ) as http:
        client = MarketplaceClient(http)
        with pytest.raises((httpx.ReadTimeout, ValueError)):
            await run_review_cycle(client, client, "project", client)
    assert len(requests) == (5 if failure == "foreign" else 6)
    assert not any(
        request.url.path.endswith("/review") and request.method == "POST"
        for request in requests
    )
