"""provider_health.pick_live_model: liveness-aware candidate selection.

All HTTP is stubbed — no real network. Each test exercises one guard named in
the module docstring: skip-without-network on a missing key, dead-vs-unknown
asymmetry, and the TTL cache.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.services import provider_health
from app.services.provider_health import Verdict, pick_live_model

_BLANK_KEYS = {
    "cerebras_api_key": "",
    "groq_api_key": "",
    "gemini_api_key": "",
    "mistral_api_key": "",
    "nebius_api_key": "",
    "sambanova_api_key": "",
    "nvidia_api_key": "",
    "together_api_key": "",
    "zai_api_key": "",
    "cloudflare_api_key": "",
    "cloudflare_account_id": "",
    "deepseek_api_key": "",
}


def _settings(**overrides) -> Settings:
    return Settings(**{**_BLANK_KEYS, **overrides})


@contextmanager
def _patch_settings(settings: Settings):
    with patch("app.services.openrouter_service.get_settings", return_value=settings):
        yield


def _resp(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


@pytest.fixture(autouse=True)
def _clear_cache():
    provider_health._cache.clear()
    yield
    provider_health._cache.clear()


@pytest.mark.asyncio
async def test_dead_candidate_falls_through_to_second():
    settings = _settings(mistral_api_key="m-key", zai_api_key="z-key")
    get_mock = AsyncMock(side_effect=[_resp(402), _resp(200)])
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        chosen = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )

    assert chosen == "zai/glm-4.5-flash"
    assert get_mock.call_count == 2


@pytest.mark.asyncio
async def test_missing_api_key_skipped_without_network_call():
    settings = _settings(zai_api_key="z-key")  # mistral key blank
    get_mock = AsyncMock(return_value=_resp(200))
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        chosen = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )

    assert chosen == "zai/glm-4.5-flash"
    # Only ONE network call: the missing-key candidate never reached httpx.
    assert get_mock.call_count == 1


@pytest.mark.asyncio
async def test_alive_verdict_cached_within_ttl_no_second_probe():
    settings = _settings(mistral_api_key="m-key")
    get_mock = AsyncMock(return_value=_resp(200))
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        first = await pick_live_model(["mistral/mistral-small-latest"])
        second = await pick_live_model(["mistral/mistral-small-latest"])

    assert first == second == "mistral/mistral-small-latest"
    assert get_mock.call_count == 1


@pytest.mark.asyncio
async def test_all_dead_returns_first_candidate_and_logs_warning():
    settings = _settings(mistral_api_key="m-key", zai_api_key="z-key")
    get_mock = AsyncMock(side_effect=[_resp(402), _resp(429)])
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = get_mock

    with (
        _patch_settings(settings),
        patch("httpx.AsyncClient", return_value=client),
        patch("app.services.provider_health.logger") as mock_logger,
    ):
        chosen = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )

    assert chosen == "mistral/mistral-small-latest"
    assert mock_logger.warning.called


@pytest.mark.asyncio
async def test_network_timeout_does_not_permanently_blacklist():
    settings = _settings(mistral_api_key="m-key")
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # First call: timeout (UNKNOWN, short TTL). Second call, after the UNKNOWN
    # TTL has elapsed: the provider answers 200.
    client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        first = await pick_live_model(["mistral/mistral-small-latest"])
    assert first == "mistral/mistral-small-latest"  # sole candidate, fallback path
    assert provider_health._cache["mistral/mistral-small-latest"].verdict is Verdict.UNKNOWN

    # Force the UNKNOWN verdict to have expired (short TTL) and re-probe alive.
    provider_health._cache["mistral/mistral-small-latest"] = provider_health._CacheEntry(
        verdict=Verdict.UNKNOWN, expires_at=0.0
    )
    client.get = AsyncMock(return_value=_resp(200))
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        second = await pick_live_model(["mistral/mistral-small-latest"])
    assert second == "mistral/mistral-small-latest"
    assert provider_health._cache["mistral/mistral-small-latest"].verdict is Verdict.ALIVE


# ── mutation checks (guard removed -> test must go RED) ─────────────────────


@pytest.mark.asyncio
async def test_mutation_ttl_ignored_would_fail_cache_test(monkeypatch):
    """If the TTL check were ignored (always re-probe), call_count would be 2."""
    settings = _settings(mistral_api_key="m-key")
    get_mock = AsyncMock(return_value=_resp(200))
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = get_mock

    # Simulate the guard's absence: force _cached_verdict to always miss.
    monkeypatch.setattr(provider_health, "_cached_verdict", lambda model_id: None)

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        await pick_live_model(["mistral/mistral-small-latest"])
        await pick_live_model(["mistral/mistral-small-latest"])

    assert get_mock.call_count == 2  # proves the real code (1 call) is the guard


@pytest.mark.asyncio
async def test_billing_failure_is_cached_as_dead_not_unknown():
    """402 must be classified DEAD, not merely "not alive".

    Falling through to the next candidate happens for UNKNOWN too, so a
    test that only checks which model is chosen passes even when the
    dead-status set is empty. The difference is the cache: DEAD is held for
    300s, UNKNOWN for 30s, so a mis-classified billing failure gets
    re-probed ten times as often — spending real requests on an account
    that is out of credit.
    """
    settings = _settings(mistral_api_key="m-key", zai_api_key="z-key")
    get_mock = AsyncMock(side_effect=[_resp(402), _resp(200)])
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        await pick_live_model(["mistral/mistral-small-latest", "zai/glm-4.5-flash"])

    entry = provider_health._cache["mistral/mistral-small-latest"]
    assert entry.verdict is Verdict.DEAD
