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

import os
from collections.abc import Callable, Iterable
from typing import Any

from pydantic_ai import Agent
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


def _stub_tools(agent: Agent[Any, Any], tool_names: Iterable[str]) -> None:
    """Register no-op tools so FunctionModel calls do not 422."""
    for name in tool_names:

        def make(n: str) -> Callable[..., str]:
            def stub(**kwargs: Any) -> str:
                return f"[stub:{n}] ok"

            stub.__name__ = n
            return stub

        agent.tool_plain(make(name), name=name)


def _trace_from_messages(messages: list[ModelMessage]) -> list[ToolCall]:
    trace: list[ToolCall] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    args = part.args if isinstance(part.args, dict) else {}
                    trace.append(ToolCall(name=part.tool_name, args=args))
    return trace


def _build_real_llm_model() -> Any:
    """Return a model identifier string for OpenRouter real-LLM runs.

    The agent-runner uses OpenRouter; pydantic-ai reads OPENAI_API_KEY and
    OPENAI_BASE_URL from environment. Callers must set OPENROUTER_API_KEY.
    """
    model_name = os.environ.get("REAL_LLM_MODEL", _REAL_LLM_MODEL_DEFAULT)
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
        result = await agent.run("kick off your scheduled task")
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
