"""Eval runner: scripted FunctionModel produces a tool-call trace per case.

Why FunctionModel (not TestModel): TestModel calls every registered tool with
default args, which doesn't model the realistic ordering / shell-escaping bugs
we want to catch. FunctionModel lets each case script the exact sequence
(good vs anti-pattern) so evaluators can be validated against known fixtures.

Pairs cleanly with real-LLM eval later -- swap `model=FunctionModel(scripted)`
for `model="openai:gpt-oss-120b:free"` and the evaluators stay identical.

Real-LLM mode is enabled by setting ``REAL_LLM=1`` in the environment.
The model is read from ``REAL_LLM_MODEL`` (default: openai:gpt-oss-120b:free).
OpenRouter credentials come from ``OPENROUTER_API_KEY``.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .evaluators import AgentRun, ToolCall


ScriptStep = tuple[str, dict[str, Any]] | str
"""Either ('tool_name', args) for a tool call, or a plain text final response."""

_REAL_LLM_MODEL_DEFAULT: str = "openai:gpt-oss-120b:free"


def _make_scripted_function(
    steps: list[ScriptStep],
) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    """Build a FunctionModel callback that emits the next step on each call.

    Uses a mutable dict as closure state so the same function object is
    deterministic across multiple calls within one run (cursor advances).
    Two runs created with independent calls to this factory are independent.
    """
    cursor: dict[str, int] = {"i": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        i = cursor["i"]
        if i >= len(steps):
            return ModelResponse(parts=[TextPart(content="done.")])
        step = steps[i]
        cursor["i"] = i + 1
        if isinstance(step, str):
            return ModelResponse(parts=[TextPart(content=step)])
        name, args = step
        return ModelResponse(
            parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=f"call_{i}")]
        )

    return fn


_REDDIT_MOCK = json.dumps([
    {"sub": "SaaS", "title": "Frustrated with manual invoice reconciliation every month", "link": "https://reddit.com/r/SaaS/1"},
    {"sub": "SaaS", "title": "Wish there was a tool to auto-categorise support tickets by sentiment", "link": "https://reddit.com/r/SaaS/2"},
    {"sub": "startups", "title": "Struggling to track which cold emails actually convert", "link": "https://reddit.com/r/startups/3"},
    {"sub": "startups", "title": "Anyone else hate manually updating CRM after every call?", "link": "https://reddit.com/r/startups/4"},
    {"sub": "webdev", "title": "How do I handle cron job failures silently breaking production?", "link": "https://reddit.com/r/webdev/5"},
])

_PROJECTS_MOCK = json.dumps({"items": [], "total": 0})
_POST_OK = json.dumps({"id": "mock-id-123", "status": "created"})
_HEARTBEAT_OK = json.dumps({"status": "ok", "received": True})


def _smart_stub(name: str) -> Callable[..., str]:
    """Return realistic mock data keyed on tool name + command content."""

    def stub(**kwargs: Any) -> str:
        if name == "write_file":
            return "ok"
        if name in ("execute", "http_get", "http_post"):
            # Flatten all arg values to a single string for pattern matching.
            flat = " ".join(
                str(v) for val in kwargs.values()
                for v in (val if isinstance(val, list) else [val])
            )
            if "reddit.com" in flat:
                return _REDDIT_MOCK
            if "/api/v1/agents/projects" in flat and "POST" not in flat and "-X POST" not in flat:
                return _PROJECTS_MOCK
            if "/api/v1/agents/projects" in flat:
                return _POST_OK
            if "/api/v1/blog/posts" in flat:
                return _POST_OK
            if "/api/v1/agents/heartbeat" in flat:
                return _HEARTBEAT_OK
        return f"[stub:{name}] ok"

    stub.__name__ = name
    return stub


def _stub_tools(agent: Agent[Any, Any], tool_names: Iterable[str]) -> None:
    """Register smart-stub tools: realistic mock responses for real-LLM runs."""
    for name in tool_names:
        agent.tool_plain(_smart_stub(name), name=name)


def _trace_from_messages(messages: list[ModelMessage]) -> list[ToolCall]:
    trace: list[ToolCall] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    raw = part.args
                    if isinstance(raw, dict):
                        args = raw
                    elif isinstance(raw, str):
                        try:
                            parsed = json.loads(raw)
                            args = parsed if isinstance(parsed, dict) else {}
                        except Exception:
                            args = {}
                    else:
                        try:
                            args = dict(raw)  # type: ignore[arg-type]
                        except Exception:
                            args = {}
                    trace.append(ToolCall(name=part.tool_name, args=args))
    return trace


def _build_real_llm_model() -> Any:
    """Return a model identifier string for OpenRouter real-LLM runs.

    The agent-runner uses OpenRouter; pydantic-ai reads OPENAI_API_KEY and
    OPENAI_BASE_URL from environment. Callers must set OPENROUTER_API_KEY.

    pydantic-ai requires a provider prefix (e.g. ``openai:``) to route to the
    correct backend. When REAL_LLM_MODEL contains a slash but no colon-prefix
    (bare OpenRouter model IDs like ``nvidia/nemotron-...``), we prepend
    ``openai:`` so pydantic-ai sends the request to the configured
    OPENAI_BASE_URL (pointing at openrouter.ai/api/v1).
    """
    model_name = os.environ.get("REAL_LLM_MODEL", _REAL_LLM_MODEL_DEFAULT)
    # pydantic-ai requires a "<provider>:<model>" form.  OpenRouter bare model
    # IDs (e.g. "nvidia/nemotron-3-super-120b-a12b:free") contain a colon only
    # as the ":free" suffix -- not a provider prefix.  Detect this by checking
    # whether the part before the first colon contains a slash (provider names
    # never do).
    prefix, _, _ = model_name.partition(":")
    if "/" in prefix:
        # Bare OpenRouter ID -- route via the openai-compatible endpoint.
        model_name = f"openai:{model_name}"
    return model_name


async def run_scripted(
    system_prompt: str,
    steps: list[ScriptStep],
    tools: Iterable[str],
) -> AgentRun:
    """Run an agent with scripted FunctionModel responses; return AgentRun trace.

    Deterministic: each call to ``_make_scripted_function`` creates an
    independent cursor, so concurrent test runs are race-free.
    """
    try:
        agent: Agent[Any, str] = Agent(
            model=FunctionModel(_make_scripted_function(steps)),
            instructions=system_prompt,
        )
        _stub_tools(agent, tools)
        result = await agent.run("kick off your scheduled task")
        trace = _trace_from_messages(result.all_messages())
        return AgentRun(response=str(result.output), tool_calls=trace)
    except Exception as exc:
        return AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")


async def run_real_llm(
    system_prompt: str,
    tools: Iterable[str],
) -> AgentRun:
    """Run an agent with a real LLM via OpenRouter.

    Requires OPENROUTER_API_KEY, OPENAI_BASE_URL=https://openrouter.ai/api/v1.
    Only called when REAL_LLM=1 is set; skipped otherwise via pytest marker.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for real-LLM runs")

    model = _build_real_llm_model()

    try:
        agent: Agent[Any, str] = Agent(
            model=model,
            instructions=system_prompt,
        )
        _stub_tools(agent, tools)
        result = await agent.run(
            "kick off your scheduled task",
            usage_limits=UsageLimits(request_limit=200),
        )
        trace = _trace_from_messages(result.all_messages())
        usage = result.usage()
        cost_usd: float | None = None
        if usage and hasattr(usage, "total_tokens") and usage.total_tokens:
            # Rough OpenRouter estimate: $0.50/1M tokens for free-tier models.
            cost_usd = usage.total_tokens / 1_000_000 * 0.50
        metadata: dict[str, Any] = {}
        if cost_usd is not None:
            metadata["cost_usd"] = cost_usd
        return AgentRun(response=str(result.output), tool_calls=trace, metadata=metadata)
    except Exception as exc:
        return AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")
