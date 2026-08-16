"""Liveness-aware model selection — pick the first candidate that actually works.

A hardcoded model constant goes stale the moment its provider runs out of
credit or gets rate-limited (mistral 402, zai 429 — both happened within one
week). This module probes candidates in order and caches the verdict briefly,
so callers stop spending a real request on a provider already known dead
without also re-probing a healthy one on every call.

Reuses ``OpenRouterService.resolve_provider`` for key lookup and base_url
routing — this module never reimplements that.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

import httpx
from loguru import logger

from app.services.battle_judges import auth_headers, error_shaped_200, wire_model_name
from app.services.openrouter_service import OpenRouterService

# DEAD verdicts are billing/auth failures: retrying them wastes a real request
# on a provider that cannot answer regardless of how soon we ask again, so
# they are cached for the full window. UNKNOWN (network error/timeout) is a
# transient signal and must NOT blacklist a provider that may already be back
# — cached for a fraction of the window so the next call re-probes soon.
ALIVE_TTL_SECONDS = 300.0
DEAD_TTL_SECONDS = 300.0
UNKNOWN_TTL_SECONDS = 30.0

PROBE_TIMEOUT_SECONDS = 8.0

_DEAD_STATUS_CODES = frozenset({401, 402, 403, 429})


class Verdict(Enum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class _CacheEntry:
    verdict: Verdict
    expires_at: float


# In-memory verdict cache, keyed by candidate model id. Module-level: every
# caller (harvester, validator) shares one liveness picture instead of each
# re-probing the same provider independently.
_cache: dict[str, _CacheEntry] = {}


def _cached_verdict(model_id: str) -> Verdict | None:
    entry = _cache.get(model_id)
    if entry is None or entry.expires_at <= time.time():
        return None
    return entry.verdict


def _store_verdict(model_id: str, verdict: Verdict) -> None:
    ttl = {
        Verdict.ALIVE: ALIVE_TTL_SECONDS,
        Verdict.DEAD: DEAD_TTL_SECONDS,
        Verdict.UNKNOWN: UNKNOWN_TTL_SECONDS,
    }[verdict]
    _cache[model_id] = _CacheEntry(verdict=verdict, expires_at=time.time() + ttl)


async def _probe(base_url: str, api_key: str, model_id: str) -> Verdict:
    """One cheap liveness check: a one-token completion. Never raises.

    Not GET /models. config.py records why: moonshot and deepseek both
    answered 200 there while refusing every completion (429 "insufficient
    balance", 402), so the catalogue read kept an outage invisible until a
    battle needed a token. It is also blind in the other direction —
    measured 2026-08-16, z.ai's catalogue does not list glm-4.5-flash at
    all (openrouter_service.py:181-187) while the model completes normally,
    so a catalogue verdict is about a different model than the one asked for.
    """
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=auth_headers(api_key),
                json={
                    "model": wire_model_name(model_id),
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
        if resp.status_code == 200:
            # llm7's keyless rate limit answers 200 with an error-shaped body
            # (see call_judge_model / error_shaped_200) — a status-code-only
            # check would cache a rate-limited model as ALIVE for the full
            # 300s window and elect it a judge seat while every real call
            # fails. UNKNOWN (30s TTL) is right: a rate limit is transient.
            if error_shaped_200(resp.json()) is not None:
                return Verdict.UNKNOWN
            return Verdict.ALIVE
        if resp.status_code in _DEAD_STATUS_CODES:
            return Verdict.DEAD
        return Verdict.UNKNOWN
    except httpx.HTTPError:
        return Verdict.UNKNOWN


async def pick_live_model(candidates: list[str]) -> str:
    """Return the first candidate whose provider is currently reachable.

    A candidate with no configured API key is skipped without any network
    call. Verdicts are cached per model id (see module TTLs). If none of the
    candidates are alive, the first candidate is returned anyway — so the
    caller makes a real request and produces a real upstream error/log line
    instead of silently doing nothing.
    """
    svc = OpenRouterService()
    verdicts: dict[str, str] = {}

    for model_id in candidates:
        creds = svc.resolve_provider(model_id)
        if creds is None:
            verdicts[model_id] = "no_api_key"
            continue

        cached = _cached_verdict(model_id)
        if cached is not None:
            verdicts[model_id] = cached.value
            if cached is Verdict.ALIVE:
                return model_id
            continue

        verdict = await _probe(creds["base_url"], creds["api_key"], model_id)
        _store_verdict(model_id, verdict)
        verdicts[model_id] = verdict.value
        if verdict is Verdict.ALIVE:
            return model_id

    logger.warning("no live candidate among {}; falling back to first: {}", verdicts, candidates[0])
    return candidates[0]
