"""Unit tests for Z.AI and Cloudflare Workers AI extra-provider support."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.services.openrouter_service import OpenRouterService, _provider_prefix

# All extra-provider key fields zeroed so the test environment's real .env keys
# don't activate unrelated providers during model-discovery tests.
_BLANK_PROVIDER_KEYS = {
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
}


def _settings(**overrides) -> Settings:
    """Settings with all provider keys blank except the given overrides."""
    return Settings(**{**_BLANK_PROVIDER_KEYS, **overrides})


@contextmanager
def _patch_settings(svc_module_settings: Settings):
    """Patch get_settings() used inside openrouter_service to return given Settings."""
    with patch(
        "app.services.openrouter_service.get_settings",
        return_value=svc_module_settings,
    ):
        yield


def _mock_models_response(model_ids: list[str]) -> MagicMock:
    """Build a mock httpx response for a provider /models call."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": [{"id": mid, "context_window": 131072} for mid in model_ids]
    }
    return resp


@contextmanager
def _patch_models_fetch(captured_urls: list[str], resp: MagicMock):
    """Patch httpx.AsyncClient so every GET records its URL and returns `resp`."""
    async def _get(url, *args, **kwargs):
        captured_urls.append(url)
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _get
    with patch("httpx.AsyncClient", return_value=mock_client):
        yield


# ── resolve_provider ────────────────────────────────────────────────────────


def test_resolve_provider_zai_returns_base_url():
    settings = _settings(zai_api_key="zai-secret")
    svc = OpenRouterService()
    with _patch_settings(settings):
        info = svc.resolve_provider("zai/glm-4.5-flash")
    assert info == {"base_url": "https://api.z.ai/api/paas/v4", "api_key": "zai-secret"}


def test_resolve_provider_cloudflare_composes_account_id():
    settings = _settings(cloudflare_api_key="cf-secret", cloudflare_account_id="acct123")
    svc = OpenRouterService()
    with _patch_settings(settings):
        info = svc.resolve_provider("cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    assert info is not None
    assert info["base_url"] == "https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1"
    assert "{account_id}" not in info["base_url"]
    assert info["api_key"] == "cf-secret"


def test_resolve_provider_cloudflare_requires_account_id():
    settings = _settings(cloudflare_api_key="cf-secret")  # no account id
    svc = OpenRouterService()
    with _patch_settings(settings):
        assert svc.resolve_provider("cloudflare/@cf/meta/llama-3.3") is None


def test_resolve_provider_cloudflare_requires_api_key():
    settings = _settings(cloudflare_account_id="acct123")  # no api key
    svc = OpenRouterService()
    with _patch_settings(settings):
        assert svc.resolve_provider("cloudflare/@cf/meta/llama-3.3") is None


# ── is_allowed ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_allowed_cloudflare_needs_both():
    svc = OpenRouterService()
    with _patch_settings(_settings(cloudflare_api_key="k")):
        assert await svc.is_allowed("cloudflare/@cf/meta/x") is False
    with _patch_settings(_settings(cloudflare_api_key="k", cloudflare_account_id="a")):
        assert await svc.is_allowed("cloudflare/@cf/meta/x") is True


@pytest.mark.asyncio
async def test_is_allowed_zai_needs_key():
    svc = OpenRouterService()
    with _patch_settings(_settings()):
        assert await svc.is_allowed("zai/glm-4.5-flash") is False
    with _patch_settings(_settings(zai_api_key="k")):
        assert await svc.is_allowed("zai/glm-4.5-flash") is True


# ── model discovery / filtering ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zai_serves_static_flash_models_without_fetch():
    # Z.AI's /models hides the free Flash family; serve it from a static list
    # and never hit the network for this provider.
    settings = _settings(zai_api_key="zai-secret")
    svc = OpenRouterService()
    resp = _mock_models_response(["glm-4.6", "glm-5"])  # what /models WOULD return
    urls: list[str] = []
    with _patch_settings(settings), _patch_models_fetch(urls, resp):
        models = await svc._extra_provider_models()
    ids = {m["id"] for m in models}
    assert "zai/glm-4.7-flash" in ids
    assert "zai/glm-4.5-flash" in ids
    assert "zai/glm-4.6" not in ids  # paid model from /models never emitted
    assert urls == []  # no /models fetch for a static-model provider


@pytest.mark.asyncio
async def test_cloudflare_models_use_composed_base_url():
    settings = _settings(cloudflare_api_key="cf", cloudflare_account_id="acct123")
    svc = OpenRouterService()
    resp = _mock_models_response(["@cf/meta/llama-3.3-70b-instruct-fp8-fast"])
    urls: list[str] = []
    with _patch_settings(settings), _patch_models_fetch(urls, resp):
        models = await svc._extra_provider_models()
    ids = {m["id"] for m in models}
    assert "cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast" in ids
    assert urls == [
        "https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1/models"
    ]


@pytest.mark.asyncio
async def test_cloudflare_skipped_without_account_id():
    settings = _settings(cloudflare_api_key="cf")  # missing account id
    svc = OpenRouterService()
    resp = _mock_models_response(["@cf/meta/llama"])
    urls: list[str] = []
    with _patch_settings(settings), _patch_models_fetch(urls, resp):
        models = await svc._extra_provider_models()
    assert models == []
    assert urls == []  # provider not fetched at all


@pytest.mark.asyncio
async def test_provider_models_fetch_failure_returns_empty():
    settings = _settings(cerebras_api_key="cb-secret")
    svc = OpenRouterService()

    async def _boom(url, *args, **kwargs):
        raise httpx.ConnectError("offline")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = _boom
    with _patch_settings(settings), patch("httpx.AsyncClient", return_value=mock_client):
        models = await svc._extra_provider_models()
    assert models == []


@pytest.mark.asyncio
async def test_provider_models_unexpected_shape_returns_empty():
    settings = _settings(cerebras_api_key="cb-secret")
    svc = OpenRouterService()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = ["unexpected", "list", "shape"]
    urls: list[str] = []
    with _patch_settings(settings), _patch_models_fetch(urls, resp):
        models = await svc._extra_provider_models()
    assert models == []


# ── resolve_model: extra-provider passthrough must never hit the fallback ─────


@pytest.mark.asyncio
async def test_resolve_model_zai_passes_through_not_nemotron():
    """A zai/-prefixed model is prefix-routed and must survive resolve unchanged.

    Regression: zai/glm-4.5-flash agents (qaagent, rsbuilderagent) were being
    downgraded to the dead nemotron fallback, producing upstream 'Unknown Model'
    (Z.AI error 1211) on every cron cycle.
    """
    svc = OpenRouterService()
    resolved = await svc.resolve_model("zai/glm-4.5-flash")
    assert resolved == "zai/glm-4.5-flash"
    # Never downgraded to the dead OpenRouter nemotron slug.
    assert resolved != "nvidia/nemotron-3-super-120b-a12b:free"


@pytest.mark.asyncio
async def test_resolve_model_zai_passthrough_robust_to_casing_and_whitespace():
    """Prefix routing is whitespace-stripped and case-insensitive.

    A model id stored with stray spacing or mixed case must still route to its
    provider instead of falling through to FALLBACK_MODEL.
    """
    svc = OpenRouterService()
    for variant in (" zai/glm-4.5-flash ", "ZAI/glm-4.5-flash", "Zai/glm-4.7-flash"):
        resolved = await svc.resolve_model(variant)
        assert resolved == variant
        # Routed to its own provider, never the dead OpenRouter nemotron slug.
        assert resolved != "nvidia/nemotron-3-super-120b-a12b:free"


@pytest.mark.asyncio
async def test_resolve_model_dead_nemotron_free_not_a_passthrough_default():
    """The dead OpenRouter nemotron ':free' slug must not be silently injected.

    resolve_model only returns FALLBACK_MODEL for explicitly blocked OpenRouter
    models; a healthy extra-provider model never resolves to the dead slug.
    """
    svc = OpenRouterService()
    dead = "nvidia/nemotron-3-super-120b-a12b:free"
    # An unblocked, non-extra-provider model passes through as-is (no fallback).
    assert await svc.resolve_model("openai/gpt-oss-120b:free") == "openai/gpt-oss-120b:free"
    # zai model never becomes the dead nemotron slug.
    assert await svc.resolve_model("zai/glm-4.5-flash") != dead
    # And the dead slug is no longer the configured fallback.
    assert OpenRouterService.FALLBACK_MODEL != dead


def test_fallback_model_is_not_self_blocked():
    """The fallback must never be a member of the OpenRouter-blocked set.

    Regression guard: a previous fallback ('nvidia/nemotron…:free') carried a
    provider prefix in EXTRA_PROVIDERS (nvidia) yet pointed at a privacy-blocked
    OpenRouter slug — a fallback that resolves to a provider we hold no key for,
    or to a self-blocked model, is broken by construction.
    """
    fallback = OpenRouterService.FALLBACK_MODEL
    assert fallback not in OpenRouterService.BLOCKED_MODELS
    # The prefix routes to a real extra provider (Z.AI), not a dead/stale one.
    prefix = _provider_prefix(fallback)
    assert prefix in OpenRouterService.EXTRA_PROVIDERS


def test_fallback_model_resolves_to_a_live_provider():
    """resolve_provider(FALLBACK_MODEL) must yield a non-None provider dict.

    With the fallback's api key configured, the fallback always has a reachable
    base_url + key — otherwise a downgrade to the fallback would dead-end.
    """
    settings = _settings(zai_api_key="zai-secret")
    svc = OpenRouterService()
    with _patch_settings(settings):
        info = svc.resolve_provider(OpenRouterService.FALLBACK_MODEL)
    assert info is not None
    assert info["base_url"] == "https://api.z.ai/api/paas/v4"
    assert info["api_key"] == "zai-secret"


@pytest.mark.asyncio
async def test_resolve_model_blocked_openrouter_falls_back():
    """Only genuinely blocked OpenRouter models are swapped for the fallback."""
    svc = OpenRouterService()
    blocked = next(iter(OpenRouterService.BLOCKED_MODELS))
    assert await svc.resolve_model(blocked) == OpenRouterService.FALLBACK_MODEL


def test_resolve_provider_zai_robust_to_casing_and_whitespace():
    """resolve_provider routes a slightly-off zai model to its own base_url.

    Without this, a stray-spaced zai model would get no provider_base_url and be
    sent to the default OpenRouter endpoint, which rejects it.
    """
    settings = _settings(zai_api_key="zai-secret")
    svc = OpenRouterService()
    with _patch_settings(settings):
        info = svc.resolve_provider(" ZAI/glm-4.5-flash ")
    assert info == {"base_url": "https://api.z.ai/api/paas/v4", "api_key": "zai-secret"}
