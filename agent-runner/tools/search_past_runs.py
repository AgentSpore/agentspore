"""`search_past_runs` tool — let hosted agents recall their own prior runs.

First step of the AgentSpore "context engine" pattern (AIE London 2026 talk
#12 / Walsenuk-Unblocked): instead of every run starting as a blank slate,
the agent can query the `replay_cases` substrate (1% sampled prod traces,
written by ``agent-runner/replay_sampler.py``) for similar past tasks.

Phase 1 (this file): explicit tool call — the agent decides when to search.
Phase 2 (next iter): pre-run automatic research-packet builder.

The tool is wired into ``routes/agents.py`` for every hosted agent. The
caller's ``agent_handle`` is bound at agent-start time via a closure factory
(``make_search_past_runs_tool``) so the LLM does not see / cannot spoof it.
"""
from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from pydantic_ai import Tool

from config import get_settings


def make_search_past_runs_tool(agent_handle: str) -> Tool:
    """Build a pydantic-ai ``Tool`` bound to one hosted agent's handle.

    The handle is captured in the closure rather than passed as a tool arg
    so the model has no opportunity to query another agent's history.
    """

    async def search_past_runs(
        query: str,
        limit: int = 5,
        status_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search this agent's prior runs by keyword match across input + output.

        Use when you want to recall how you handled a similar task before — for
        example "blog post about reddit AI" or "github webhook 500 error". The
        platform stores ~1% of completed runs as searchable replay cases.

        Args:
            query: 2..500 char keyword string. Matched with case-insensitive
                substring against ``output_text`` and ``input_messages``.
            limit: max results to return (1..20, default 5).
            status_filter: optional ``"completed"`` | ``"failed"`` | ``"truncated"``
                filter. ``None`` returns all statuses.

        Returns:
            list of summary dicts, sorted by recency (newest first). Each entry
            contains: ``id``, ``captured_at``, ``agent_handle``, ``model``,
            ``status``, ``input_summary`` (200-char snippet), ``output_text``
            (full), ``tool_calls_count``, ``duration_ms``.

            Returns ``[]`` on any error — the tool never raises so the agent
            loop is not interrupted by transient network issues.
        """
        settings = get_settings()
        params: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "agent_handle": agent_handle,
        }
        if status_filter:
            params["status"] = status_filter

        url = f"{settings.agentspore_url}/api/v1/internal/replay-cases/search"
        headers = {"X-Runner-Key": settings.runner_key}

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning(
                "search_past_runs failed for handle={} query={!r}: {}",
                agent_handle,
                query,
                exc,
            )
            return []

    return Tool(
        search_past_runs,
        name="search_past_runs",
        description=(
            "Search this agent's prior runs by keyword. Useful for recalling "
            "how a similar task was handled before instead of starting blank."
        ),
        takes_ctx=False,
    )
