"""Eval runner: scripted FunctionModel produces a tool-call trace per case.

Why FunctionModel (not TestModel): TestModel calls every registered tool with
default args, which doesn't model the realistic ordering / shell-escaping bugs
we want to catch. FunctionModel lets each case script the exact sequence
(good vs anti-pattern) so evaluators can be validated against known fixtures.

Pairs cleanly with real-LLM eval later — swap `model=FunctionModel(scripted)`
for `model="openai:gpt-oss-120b:free"` and the evaluators stay identical.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .evaluators import AgentRun, ToolCall


ScriptStep = tuple[str, dict[str, Any]] | str
"""Either ('tool_name', args) for a tool call, or a plain text final response."""


def _make_scripted_function(steps: list[ScriptStep]) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    """Build a FunctionModel callback that emits the next step on each call."""
    cursor = {"i": 0}

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
    """Register no-op tools so FunctionModel calls don't 422."""
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


async def run_scripted(system_prompt: str, steps: list[ScriptStep], tools: Iterable[str]) -> AgentRun:
    """Run an agent with scripted FunctionModel responses; return AgentRun trace."""
    try:
        agent = Agent(
            model=FunctionModel(_make_scripted_function(steps)),
            instructions=system_prompt,
        )
        _stub_tools(agent, tools)
        result = await agent.run("kick off your scheduled task")
        trace = _trace_from_messages(result.all_messages())
        return AgentRun(response=str(result.output), tool_calls=trace)
    except Exception as exc:
        return AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")
