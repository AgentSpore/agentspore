"""Prod-trace sampling: 1% of completed chat runs are sent to backend for offline eval.

Fire-and-forget — never blocks or raises in the caller.
Pattern from Phil Hetzel (Braintrust) at AIE London 2026:
"prod traces → offline eval dataset closes the feedback loop".
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
from loguru import logger

from config import RunnerSettings, get_settings


async def _post_replay_case(payload: dict[str, Any], settings: RunnerSettings) -> None:
    """POST one replay case to the backend.  Best-effort; logs on failure."""
    url = f"{settings.agentspore_url}/api/v1/internal/replay-cases"
    headers = {"X-Runner-Key": settings.runner_key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code not in (200, 201):
                logger.warning(
                    "replay_sampler: backend returned {} for trace_id={}",
                    resp.status_code,
                    payload.get("trace_id"),
                )
    except Exception as exc:
        logger.warning("replay_sampler: failed to post replay case: {}", repr(exc))


def maybe_sample(
    *,
    hosted_agent_id: str,
    agent_handle: str,
    model: str,
    trace_id: str | None,
    input_messages: list[dict[str, Any]],
    output_text: str | None,
    tool_calls: list[dict[str, Any]],
    started_at: float,  # time.monotonic() at chat start
    status: str,  # 'completed' | 'failed' | 'truncated'
    metadata: dict[str, Any] | None = None,
    settings: RunnerSettings | None = None,
) -> None:
    """Randomly sample a chat run and fire-and-forget it to the backend.

    Must be called from an async context (uses asyncio.create_task).
    Swallows all errors — never raises.
    """
    if settings is None:
        settings = get_settings()

    if not settings.replay_enabled:
        return
    if random.random() >= settings.replay_sample_rate:
        return

    duration_ms = int((time.monotonic() - started_at) * 1000)

    payload: dict[str, Any] = {
        "hosted_agent_id": hosted_agent_id,
        "agent_handle": agent_handle,
        "model": model,
        "trace_id": trace_id,
        "input_messages": input_messages,
        "output_text": output_text,
        "tool_calls": tool_calls,
        "duration_ms": duration_ms,
        "status": status,
        "metadata": metadata or {},
    }

    try:
        asyncio.create_task(_post_replay_case(payload, settings))
    except RuntimeError:
        # No running event loop (e.g. called from sync test context) — skip silently
        logger.warning("replay_sampler: no event loop, skipping sample")
