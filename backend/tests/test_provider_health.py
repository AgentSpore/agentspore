"""provider_health.pick_live_model: liveness-aware candidate selection.

All HTTP is stubbed — no real network. Each test exercises one guard named in
the module docstring: skip-without-network on a missing key, dead-vs-unknown
asymmetry, and the TTL cache.
"""

from __future__ import annotations

import time
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


def _resp(status_code: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body if body is not None else {"choices": [{}]})
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
    client.post = get_mock

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
    client.post = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        chosen = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )

    assert chosen == "zai/glm-4.5-flash"
    # Only ONE network call: the missing-key candidate never reached httpx.
    assert get_mock.call_count == 1


@pytest.mark.asyncio
async def test_alive_verdict_cached_within_ttl_no_second_probe():
    # BOTH keys set: with zai unconfigured it is skipped without a probe, so
    # dropping the cached-ALIVE return would still land on mistral via the
    # fallback and the mutation would stay invisible.
    settings = _settings(mistral_api_key="m-key", zai_api_key="z-key")
    get_mock = AsyncMock(return_value=_resp(200))
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = get_mock

    # Two candidates, not one: with a single candidate the fallback returns the
    # same string even when the cached-ALIVE branch is deleted, so the test
    # would pass with the guard gone. With two, dropping that branch skips the
    # cached-alive first candidate and probes the second instead.
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        first = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )
        second = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )

    assert first == second == "mistral/mistral-small-latest"
    assert get_mock.call_count == 1


@pytest.mark.asyncio
async def test_all_dead_returns_first_candidate_and_logs_warning():
    settings = _settings(mistral_api_key="m-key", zai_api_key="z-key")
    get_mock = AsyncMock(side_effect=[_resp(402), _resp(429)])
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = get_mock

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
    client.post = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        first = await pick_live_model(["mistral/mistral-small-latest"])
    assert first == "mistral/mistral-small-latest"  # sole candidate, fallback path
    assert provider_health._cache["mistral/mistral-small-latest"].verdict is Verdict.UNKNOWN

    # Force the UNKNOWN verdict to have expired (short TTL) and re-probe alive.
    provider_health._cache["mistral/mistral-small-latest"] = provider_health._CacheEntry(
        verdict=Verdict.UNKNOWN, expires_at=0.0
    )
    client.post = AsyncMock(return_value=_resp(200))
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
    client.post = get_mock

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
    client.post = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        await pick_live_model(["mistral/mistral-small-latest", "zai/glm-4.5-flash"])

    entry = provider_health._cache["mistral/mistral-small-latest"]
    assert entry.verdict is Verdict.DEAD


@pytest.mark.asyncio
async def test_probe_sends_no_authorization_header_for_a_blank_key():
    """_probe builds its OWN headers dict — a separate call site from
    call_judge_model that needed the same fix independently, or a keyless
    provider (llm7) can never be probed at all (h11 rejects `Bearer `)."""
    captured_kwargs: dict = {}

    async def _post(_url, **kwargs):
        captured_kwargs.update(kwargs)
        return _resp(200)

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = _post

    with patch("httpx.AsyncClient", return_value=client):
        await provider_health._probe("https://api.llm7.io/v1", "", "llm7/DeepSeek-V4-Flash-0731")

    assert "Authorization" not in captured_kwargs["headers"]


@pytest.mark.asyncio
async def test_200_shaped_rate_limit_is_unknown_not_alive():
    """llm7's keyless rate limit answers HTTP 200 with an error body — the
    exact shape call_judge_model was taught to detect. _probe must classify
    it UNKNOWN (transient, 30s TTL), not ALIVE (300s TTL): caching a rate-
    limited model as alive elects it primary judge seat for five minutes
    while every real call raises JudgeTransportError (review finding 2).
    """
    settings = _settings(mistral_api_key="m-key", zai_api_key="z-key")
    rate_limited = _resp(
        200, body={"error": {"message": "Rate limit exceeded. Retry after 1 seconds."}}
    )
    get_mock = AsyncMock(side_effect=[rate_limited, _resp(200)])
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = get_mock

    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        chosen = await pick_live_model(
            ["mistral/mistral-small-latest", "zai/glm-4.5-flash"]
        )

    assert chosen == "zai/glm-4.5-flash"
    entry = provider_health._cache["mistral/mistral-small-latest"]
    assert entry.verdict is Verdict.UNKNOWN
    ttl_remaining = entry.expires_at - time.time()
    assert ttl_remaining < 60  # UNKNOWN's 30s TTL, not ALIVE's 300s


@pytest.mark.asyncio
async def test_dead_is_cached_far_longer_than_unknown():
    """The three TTL constants exist for this ratio; assert it directly.

    Nothing else measures it: the timeout test overwrites the cache entry by
    hand, so it proves expiry handling, not the durations. Collapsing the
    TTLs to one value would ship green — and then a billing failure gets
    re-probed every 30s, spending real requests on an account out of credit.
    """
    settings = _settings(mistral_api_key="m-key")
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    client.post = AsyncMock(return_value=_resp(402))
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        await pick_live_model(["mistral/mistral-small-latest"])
    dead_ttl = provider_health._cache["mistral/mistral-small-latest"].expires_at - time.time()

    provider_health._cache.clear()
    client.post = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=client):
        await pick_live_model(["mistral/mistral-small-latest"])
    unknown_ttl = provider_health._cache["mistral/mistral-small-latest"].expires_at - time.time()

    assert dead_ttl > unknown_ttl * 2
    assert unknown_ttl < 60
