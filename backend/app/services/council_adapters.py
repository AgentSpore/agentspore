"""Panelist adapters — pluggable transports for council participants.

Each adapter knows how to deliver a prompt to a panelist and wait for a response,
abstracting away the underlying transport (pure LLM API, platform WS, webhook,
hosted agent, MCP, human).
"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx
from loguru import logger


class PanelistAdapter(ABC):
    """Abstract base for all panelist transports.

    `generate(...)` takes the full discussion context and returns the panelist's
    next message as a plain string. Adapter implementations are responsible for
    their own timeouts, retries, and error handling. On failure they should
    return a short placeholder so the council can continue without blocking.
    """

    def __init__(self, panelist: dict, council: dict):
        self.panelist = panelist
        self.council = council

    @abstractmethod
    async def generate(self, system_prompt: str, messages: list[dict]) -> dict:
        """Return `{"content": str, "meta": dict}` — dict so we can record latency/tokens."""
        ...


# ── Pure LLM via OpenRouter (zero infra) ─────────────────────────────────


class PureLLMAdapter(PanelistAdapter):
    """Direct OpenRouter chat completion — no agent identity, no persistence."""

    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    TIMEOUT = 60.0
    # Retry transient upstream errors (rate limits / provider hiccups) so one
    # shared OpenRouter account doesn't wreck every council under light load.
    RETRY_STATUSES = {408, 429, 500, 502, 503, 504, 520, 522, 524}
    RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0)

    async def generate(self, system_prompt: str, messages: list[dict]) -> dict:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            return {
                "content": "[error: OPENROUTER_API_KEY not set]",
                "meta": {"error": "no_api_key", "error_kind": "config"},
            }

        model = self.panelist["model_id"]
        display_name = self.panelist.get("display_name") or model
        max_tokens = self.council.get("max_tokens_per_msg", 500)

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.8,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://agentspore.com",
            "X-Title": "AgentSpore Council",
        }

        last_status: int | None = None
        last_body: str = ""
        started = asyncio.get_event_loop().time()
        for attempt_idx in range(len(self.RETRY_DELAYS_SECONDS) + 1):
            try:
                async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                    resp = await client.post(self.OPENROUTER_URL, json=payload, headers=headers)
                if resp.status_code == 200:
                    elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    usage = data.get("usage", {})
                    return {
                        "content": content,
                        "meta": {
                            "elapsed_ms": elapsed_ms,
                            "model": model,
                            "tokens_prompt": usage.get("prompt_tokens"),
                            "tokens_completion": usage.get("completion_tokens"),
                            "attempts": attempt_idx + 1,
                        },
                    }
                last_status = resp.status_code
                last_body = resp.text[:200]
                if resp.status_code not in self.RETRY_STATUSES or attempt_idx >= len(self.RETRY_DELAYS_SECONDS):
                    break
                logger.info(
                    "PureLLMAdapter retrying {} after HTTP {} (attempt {}/{})",
                    model, resp.status_code, attempt_idx + 1, len(self.RETRY_DELAYS_SECONDS),
                )
                await asyncio.sleep(self.RETRY_DELAYS_SECONDS[attempt_idx])
            except Exception as exc:
                logger.warning("PureLLMAdapter request error for {}: {}", model, exc)
                if attempt_idx >= len(self.RETRY_DELAYS_SECONDS):
                    return {
                        "content": f"[{display_name} is unreachable — {type(exc).__name__}. Try again in a minute.]",
                        "meta": {"error": str(exc)[:200], "error_kind": "network", "model": model},
                    }
                await asyncio.sleep(self.RETRY_DELAYS_SECONDS[attempt_idx])

        elapsed_ms = int((asyncio.get_event_loop().time() - started) * 1000)
        if last_status == 429:
            friendly = (
                f"[{display_name} is rate-limited by the free tier right now. "
                "The free OpenRouter pool is shared — please retry in a few minutes.]"
            )
            kind = "rate_limit"
        elif last_status == 402:
            friendly = f"[{display_name} is out of free credits for today.]"
            kind = "quota"
        elif last_status is not None and 500 <= last_status < 600:
            friendly = f"[{display_name} upstream is flaky right now (HTTP {last_status}). Retry later.]"
            kind = "upstream"
        else:
            friendly = f"[{display_name} refused the request (HTTP {last_status}).]"
            kind = "http_error"
        logger.warning("PureLLMAdapter final failure {} status={} body={}", model, last_status, last_body[:120])
        return {
            "content": friendly,
            "meta": {
                "error": last_body,
                "error_kind": kind,
                "http_status": last_status,
                "elapsed_ms": elapsed_ms,
                "model": model,
                "attempts": len(self.RETRY_DELAYS_SECONDS) + 1,
            },
        }


# ── Platform WebSocket (real-time push to an external agent) ─────────────


class PlatformWSAdapter(PanelistAdapter):
    """Push event to a platform agent via WS/webhook/heartbeat fallback.

    Agent is expected to POST its reply to /api/v1/councils/{id}/messages within
    the turn timebox. If it doesn't, we fall back to a "[no response]" placeholder.
    """

    TURN_TIMEOUT = 30.0

    async def generate(self, system_prompt: str, messages: list[dict]) -> dict:
        from app.services.connection_manager import deliver_event
        from app.core.database import async_session_maker
        from app.repositories.council_repo import CouncilRepository

        agent_id = str(self.panelist["agent_id"])
        event = {
            "type": "council_turn",
            "council_id": str(self.council["id"]),
            "panelist_id": str(self.panelist["id"]),
            "round_num": self.council.get("current_round", 0),
            "system_prompt": system_prompt,
            "messages": messages,
            "respond_via": f"POST /api/v1/councils/{self.council['id']}/messages",
            "deadline_seconds": int(self.TURN_TIMEOUT),
        }
        try:
            await deliver_event(agent_id, event)
        except Exception as exc:
            logger.warning("council WS deliver failed: {}", exc)
            return {"content": "[no response: delivery failed]", "meta": {"error": str(exc)[:200]}}

        # Poll DB for a new message from this panelist.
        deadline = asyncio.get_event_loop().time() + self.TURN_TIMEOUT
        last_seen_count = len(messages)
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
            async with async_session_maker() as session:
                repo = CouncilRepository(session)
                latest = await repo.list_messages(str(self.council["id"]))
            for m in reversed(latest):
                if str(m.get("panelist_id") or "") == str(self.panelist["id"]) and m["round_num"] == self.council.get("current_round", 0):
                    return {"content": m["content"], "meta": {"source": "ws", "async": True}}
        return {"content": "[no response: agent timeout]", "meta": {"error": "timeout"}}
