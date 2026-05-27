"""Tests for the `search_past_runs` hosted-agent tool.

Mocks httpx so we never hit the real backend. Covers success, error swallow,
auth header propagation, and status_filter param passthrough.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.search_past_runs import make_search_past_runs_tool


def fake_httpx_client(*, status_code: int, json_payload: list[dict] | None = None, raise_exc: Exception | None = None):
    """Return (AsyncClient factory mock, inner client mock) for httpx patching.

    The factory mock replaces ``httpx.AsyncClient`` and yields an async context
    manager wrapping a client whose ``get`` is an AsyncMock — either returning
    a fake response or raising ``raise_exc``.
    """
    fake_response = MagicMock()
    fake_response.status_code = status_code
    fake_response.json = MagicMock(return_value=json_payload or [])
    if status_code >= 400:
        def _raise_for_status() -> None:
            raise RuntimeError(f"HTTP {status_code}")
        fake_response.raise_for_status = MagicMock(side_effect=_raise_for_status)
    else:
        fake_response.raise_for_status = MagicMock(return_value=None)

    client_instance = MagicMock()
    if raise_exc is not None:
        client_instance.get = AsyncMock(side_effect=raise_exc)
    else:
        client_instance.get = AsyncMock(return_value=fake_response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client_instance)
    cm.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock(return_value=cm)
    return factory, client_instance


@pytest.fixture
def bound_tool():
    return make_search_past_runs_tool("redditscout")


class TestSearchPastRuns:
    @pytest.mark.asyncio
    async def test_returns_results_on_200(self, bound_tool):
        """200 OK with a JSON list passes straight through to the caller."""
        payload = [
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "captured_at": "2026-05-25T12:00:00Z",
                "agent_handle": "redditscout",
                "model": "mistralai/mistral-nemo",
                "status": "completed",
                "input_summary": "Post a blog about reddit AI",
                "output_text": "Done. Blog post id 42.",
                "tool_calls_count": 5,
                "duration_ms": 9000,
            }
        ]
        factory, client = fake_httpx_client(status_code=200, json_payload=payload)

        with patch("tools.search_past_runs.httpx.AsyncClient", factory):
            fn = bound_tool.function
            result = await fn("blog reddit")

        assert result == payload
        assert client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_error_no_raise(self, bound_tool):
        """Any exception inside the http call is swallowed → returns []."""
        factory, _ = fake_httpx_client(
            status_code=500,
            raise_exc=RuntimeError("boom"),
        )

        with patch("tools.search_past_runs.httpx.AsyncClient", factory):
            fn = bound_tool.function
            result = await fn("anything")

        assert result == []

    @pytest.mark.asyncio
    async def test_includes_x_runner_key(self, bound_tool):
        """X-Runner-Key header is set from settings.runner_key on every call."""
        factory, client = fake_httpx_client(status_code=200, json_payload=[])

        with patch("tools.search_past_runs.httpx.AsyncClient", factory):
            fn = bound_tool.function
            await fn("query string")

        call_kwargs = client.get.await_args.kwargs
        assert "X-Runner-Key" in call_kwargs["headers"]
        assert call_kwargs["headers"]["X-Runner-Key"] == "test-runner-key"
        # agent_handle must come from the closure, NOT user-supplied
        assert call_kwargs["params"]["agent_handle"] == "redditscout"
        assert call_kwargs["params"]["q"] == "query string"

    @pytest.mark.asyncio
    async def test_respects_status_filter(self, bound_tool):
        """status_filter arg is forwarded as a query param when set, omitted otherwise."""
        factory, client = fake_httpx_client(status_code=200, json_payload=[])

        with patch("tools.search_past_runs.httpx.AsyncClient", factory):
            fn = bound_tool.function
            await fn("topic", limit=10, status_filter="failed")

        params = client.get.await_args.kwargs["params"]
        assert params["status"] == "failed"
        assert params["limit"] == 10

        # When omitted, status is NOT included
        factory2, client2 = fake_httpx_client(status_code=200, json_payload=[])
        with patch("tools.search_past_runs.httpx.AsyncClient", factory2):
            fn = bound_tool.function
            await fn("topic")
        assert "status" not in client2.get.await_args.kwargs["params"]
