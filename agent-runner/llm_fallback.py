"""LLM fallback chain for hosted agents.

Wraps raw httpx calls with automatic fallback across a ranked list of
providers and models. When the primary model returns a rate-limit (429),
server error (5xx), or times out, the next entry in the chain is tried.

Providers supported (see providers.py for full registry):
  - openrouter  — existing production provider, OPENAI_API_KEY required
  - nvidia      — NVIDIA NIM, NVIDIA_API_KEY required (free at build.nvidia.com)
  - groq        — ultra-fast inference, GROQ_API_KEY required (free tier)
  - cerebras    — wafer-scale inference, CEREBRAS_API_KEY required (free tier)
  - together    — Together AI, TOGETHER_API_KEY required ($1 free credit)

Configuration (env vars / .env):
  LLM_FALLBACK_CHAIN  — comma-separated entries, highest-priority first.
                        Format: ``provider:model_id`` or bare model_id (legacy
                        OpenRouter compat). Examples:
                          nvidia:nvidia/llama-3.1-nemotron-70b-instruct,openrouter:nvidia/nemotron-3-super-120b-a12b:free
                        Defaults to auto-built chain from active providers.
  OPENAI_API_KEY      — OpenRouter key (existing config).
  NVIDIA_API_KEY      — NVIDIA NIM key. Omit to skip NVIDIA provider.
  GROQ_API_KEY        — Groq key. Omit to skip.
  CEREBRAS_API_KEY    — Cerebras key. Omit to skip.
  TOGETHER_API_KEY    — Together key. Omit to skip.

Usage in main.py (start_agent):
  from llm_fallback import resolve_model_for_agent

  model_id = resolve_model_for_agent(requested_model)
  agent = create_deep_agent(model=f"openai:{model_id}", ...)

Usage via async fallback wrapper (raw httpx, not pydantic-ai internals):
  from llm_fallback import call_with_fallback

  response = await call_with_fallback(messages=[...], max_tokens=10)
"""

import asyncio
import os
import time
from typing import Any

import httpx
from loguru import logger

from providers import (
    PROVIDER_BY_NAME,
    build_default_chain,
    parse_chain_entry,
)

# ---------------------------------------------------------------------------
# Backward-compat: OpenRouter-only default chain for resolve_model_for_agent.
# Kept as a flat list of model IDs so existing start_agent logic is unchanged.
# ---------------------------------------------------------------------------
# NOTE: chain[0] is the resolved default for any agent whose requested model is
# neither a provider-prefixed passthrough nor an exact chain entry. It MUST be a
# proven-live model. ``zai/glm-4.5-flash`` is verified live in production
# (redditscoutagent runs on it). The previous default
# ``nvidia/nemotron-3-super-120b-a12b:free`` was removed from OpenRouter and now
# returns 400 1211 "Unknown Model", which killed every agent that fell through to
# it (qaagent, rsbuilderagent).
DEFAULT_FALLBACK_CHAIN: list[str] = [
    "zai/glm-4.5-flash",
    "openai/gpt-oss-120b:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "openai/gpt-oss-20b:free",
]

# HTTP status codes that trigger fallback to the next model.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Provider-specific error codes that indicate a shape/compatibility issue with
# the current model and should trigger fallback to the next model in the chain.
# 1214 = Z.AI "messages parameter is illegal" (trailing assistant message, etc.)
RETRYABLE_PROVIDER_ERROR_CODES: frozenset[int] = frozenset({1214})

# Substrings in error messages that indicate a retryable provider shape error.
RETRYABLE_ERROR_PATTERNS: tuple[str, ...] = ("messages parameter is illegal",)


# ---------------------------------------------------------------------------
# Chain loading
# ---------------------------------------------------------------------------

def _load_model_chain() -> list[str]:
    """Load OpenRouter-only model chain from LLM_FALLBACK_CHAIN, or use default.

    This is the legacy path used by resolve_model_for_agent. It extracts
    the model_id from each entry and returns a flat list for backward compat.
    Used only by pydantic-deep agent startup (which takes a single base_url).
    """
    raw = os.environ.get("LLM_FALLBACK_CHAIN", "").strip()
    if not raw:
        return list(DEFAULT_FALLBACK_CHAIN)
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    if not entries:
        return list(DEFAULT_FALLBACK_CHAIN)
    # Extract model_id portion from each entry for backward compat.
    models: list[str] = []
    for entry in entries:
        _provider, model_id = parse_chain_entry(entry)
        models.append(model_id)
    return models


def _load_provider_chain() -> list[tuple[str, str]]:
    """Load multi-provider chain from LLM_FALLBACK_CHAIN, or build default.

    Returns a list of (provider_name, model_id) pairs.
    Used by call_with_fallback and LLMHealthChecker.
    """
    raw = os.environ.get("LLM_FALLBACK_CHAIN", "").strip()
    if not raw:
        return build_default_chain()
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    if not entries:
        return build_default_chain()
    return [parse_chain_entry(e) for e in entries]


_EXTRA_PROVIDER_PREFIXES: frozenset[str] = frozenset(
    {"cerebras/", "groq/", "gemini/", "mistral/", "nebius/", "sambanova/", "zai/", "cloudflare/", "together/"}
)


def resolve_model_for_agent(requested: str) -> str:
    """Return the requested model if it is in the fallback chain, else chain[0].

    Called during agent start. Ensures the agent always starts with a known-good
    model even if the platform sends a stale/removed model ID.

    Provider-prefixed models (cerebras/, groq/, gemini/) are returned unchanged —
    they use their own base_url and do not need OpenRouter fallback validation.
    """
    if requested and any(requested.startswith(p) for p in _EXTRA_PROVIDER_PREFIXES):
        return requested
    chain = _load_model_chain()
    if requested and requested in chain:
        return requested
    if requested:
        logger.warning(
            "Requested model '{}' not in fallback chain — using '{}'",
            requested,
            chain[0],
        )
    return chain[0]


# ---------------------------------------------------------------------------
# Fallback error
# ---------------------------------------------------------------------------

class FallbackError(Exception):
    """Raised when all providers/models in the fallback chain are exhausted."""

    def __init__(self, attempts: list[dict]) -> None:
        self.attempts = attempts
        summary = "; ".join(
            f"{a['provider']}:{a['model']}→{a['error']}" for a in attempts
        )
        super().__init__(f"All LLM fallbacks exhausted: {summary}")


# ---------------------------------------------------------------------------
# Core fallback call
# ---------------------------------------------------------------------------

async def call_with_fallback(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 1024,
    timeout: float = 30.0,
    extra_body: dict | None = None,
) -> dict[str, Any]:
    """Call LLM providers with automatic model and provider fallback.

    Tries each (provider, model) pair from the configured chain in order.
    Falls back on:
      - httpx timeout or connection error
      - HTTP status codes in RETRYABLE_STATUS_CODES (429, 5xx)
      - JSON body with error.code in {429, 500, 502, 503, 504}

    Non-retryable errors (400 bad request, 401 auth, 404 not found) stop
    the chain immediately to avoid burning budget on a broken config.

    Inactive providers (missing key) are skipped silently.

    Args:
        messages:    OpenAI-format message list.
        max_tokens:  Token limit for the completion.
        timeout:     Per-request timeout in seconds.
        extra_body:  Additional fields merged into the request body.

    Returns:
        OpenAI-compatible response dict from the first succeeding model.

    Raises:
        FallbackError: When every provider/model in the chain fails.
    """
    chain = _load_provider_chain()
    attempts: list[dict] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        for provider_name, model_id in chain:
            provider = PROVIDER_BY_NAME.get(provider_name)
            if provider is None:
                logger.warning("Unknown provider '{}' in chain — skipping", provider_name)
                continue
            if not provider.is_active:
                logger.debug("Provider '{}' has no API key — skipping", provider_name)
                continue

            t0 = time.monotonic()
            try:
                body: dict[str, Any] = {
                    "model": model_id,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                if extra_body:
                    body.update(extra_body)

                resp = await client.post(
                    f"{provider.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {provider.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)

                if resp.status_code in RETRYABLE_STATUS_CODES:
                    error_msg = f"HTTP {resp.status_code}"
                    logger.warning(
                        "LLM fallback: provider='{}' model='{}' status={} latency={}ms — trying next",
                        provider_name,
                        model_id,
                        resp.status_code,
                        latency_ms,
                    )
                    attempts.append({
                        "provider": provider_name,
                        "model": model_id,
                        "error": error_msg,
                        "latency_ms": latency_ms,
                    })
                    continue

                data = resp.json()

                # OpenRouter (and some providers) may return 200 with an error body.
                if "error" in data:
                    err = data["error"]
                    err_code = err.get("code") if isinstance(err, dict) else None
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    _is_retryable_code = isinstance(err_code, int) and (
                        err_code in RETRYABLE_STATUS_CODES
                        or err_code in RETRYABLE_PROVIDER_ERROR_CODES
                    )
                    _is_retryable_msg = any(p in err_msg for p in RETRYABLE_ERROR_PATTERNS)
                    if _is_retryable_code or _is_retryable_msg:
                        logger.warning(
                            "LLM fallback: provider='{}' model='{}' error_code={} latency={}ms — trying next",
                            provider_name,
                            model_id,
                            err_code,
                            latency_ms,
                        )
                        attempts.append({
                            "provider": provider_name,
                            "model": model_id,
                            "error": f"API error {err_code}: {err_msg[:60]}",
                            "latency_ms": latency_ms,
                        })
                        continue
                    # Non-retryable (400, 401, 404) — log and stop
                    logger.error(
                        "LLM non-retryable error from provider='{}' model='{}': {}",
                        provider_name,
                        model_id,
                        err_msg[:120],
                    )
                    attempts.append({
                        "provider": provider_name,
                        "model": model_id,
                        "error": f"non-retryable: {err_msg[:60]}",
                        "latency_ms": latency_ms,
                    })
                    raise FallbackError(attempts)

                logger.info(
                    "LLM success: provider='{}' model='{}' latency={}ms fallbacks={}",
                    provider_name,
                    model_id,
                    latency_ms,
                    len(attempts),
                )
                return data

            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "LLM fallback: provider='{}' model='{}' network_error='{}' latency={}ms — trying next",
                    provider_name,
                    model_id,
                    type(exc).__name__,
                    latency_ms,
                )
                attempts.append({
                    "provider": provider_name,
                    "model": model_id,
                    "error": type(exc).__name__,
                    "latency_ms": latency_ms,
                })

    raise FallbackError(attempts)


# ---------------------------------------------------------------------------
# Health checker
# ---------------------------------------------------------------------------

class LLMHealthChecker:
    """Checks each provider/model in the fallback chain with a minimal probe.

    Used by GET /admin/llm-health to verify model availability before
    high-traffic events (hackathons, demos).

    Each entry in the result includes provider, model, status, latency_ms,
    and error. Inactive providers (no key) are reported as ``skipped``.
    """

    PROBE_MESSAGES: list[dict[str, str]] = [
        {"role": "user", "content": "hi"},
    ]

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    async def check_model(self, provider_name: str, model_id: str) -> dict[str, Any]:
        """Probe a single provider/model pair.

        Returns:
            {provider, model, status, latency_ms, error}
            status: "ok" | "error" | "timeout" | "skipped"
        """
        provider = PROVIDER_BY_NAME.get(provider_name)
        if provider is None:
            return {
                "provider": provider_name,
                "model": model_id,
                "status": "error",
                "latency_ms": 0,
                "error": f"Unknown provider '{provider_name}'",
            }

        if not provider.is_active:
            return {
                "provider": provider_name,
                "model": model_id,
                "status": "skipped",
                "latency_ms": 0,
                "error": f"No API key ({provider.api_key_env} not set)",
            }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{provider.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {provider.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "messages": self.PROBE_MESSAGES,
                        "max_tokens": 1,
                    },
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                data = resp.json()

                if resp.status_code >= 400 or "error" in data:
                    err = data.get("error", {})
                    msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    return {
                        "provider": provider_name,
                        "model": model_id,
                        "status": "error",
                        "latency_ms": latency_ms,
                        "error": msg[:120],
                    }

                return {
                    "provider": provider_name,
                    "model": model_id,
                    "status": "ok",
                    "latency_ms": latency_ms,
                    "error": None,
                }

        except httpx.TimeoutException as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {
                "provider": provider_name,
                "model": model_id,
                "status": "timeout",
                "latency_ms": latency_ms,
                "error": type(exc).__name__,
            }
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {
                "provider": provider_name,
                "model": model_id,
                "status": "error",
                "latency_ms": latency_ms,
                "error": f"{type(exc).__name__}: {str(exc)[:80]}",
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {
                "provider": provider_name,
                "model": model_id,
                "status": "error",
                "latency_ms": latency_ms,
                "error": str(exc)[:120],
            }

    async def check_all(self) -> list[dict[str, Any]]:
        """Probe all provider/model pairs in the chain concurrently.

        Inactive providers are included as ``skipped`` entries.
        """
        chain = _load_provider_chain()
        tasks = [self.check_model(provider_name, model_id) for provider_name, model_id in chain]
        results = await asyncio.gather(*tasks)
        return list(results)
