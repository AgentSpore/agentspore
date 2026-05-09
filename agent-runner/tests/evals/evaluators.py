"""Custom evaluators for hosted agent runs.

Output schema (`AgentRun`) captures the tool-call trace + final response so each
evaluator inspects a single dimension of behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_evals.evaluators import Evaluator, EvaluatorContext


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class AgentRun:
    response: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None


@dataclass
class NoErrors(Evaluator[Any, AgentRun]):
    """Agent loaded and ran without exception."""

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        return ctx.output.error is None


@dataclass
class CompletedTask(Evaluator[Any, AgentRun]):
    """Final response is more than a placeholder ack."""

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        text = (ctx.output.response or "").strip().lower()
        return bool(text) and text not in {"done.", "done", "ok.", "ok"}


@dataclass
class MinExecuteCount(Evaluator[Any, AgentRun]):
    """At least N execute tool calls happened (multi-step workflow)."""

    min_count: int = 2

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        n = sum(1 for tc in ctx.output.tool_calls if tc.name == "execute")
        return n >= self.min_count


@dataclass
class WriteFileBeforeCurlPost(Evaluator[Any, AgentRun]):
    """Every `curl -X POST` with a JSON body MUST use `-d @<file>` and that
    file MUST be written by a prior `write_file`. Catches shell-escaping
    anti-pattern (inline JSON) which silently breaks on nested quotes.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        written: set[str] = set()
        for tc in ctx.output.tool_calls:
            if tc.name == "write_file":
                path = tc.args.get("path") or tc.args.get("file_path") or ""
                if path.startswith("/tmp/"):
                    written.add(path)
                continue
            if tc.name != "execute":
                continue
            cmd = tc.args.get("command", "")
            if "curl" not in cmd or " -d " not in cmd:
                continue
            if "-d @" not in cmd:
                return False  # inline JSON → anti-pattern
            ref = cmd.split("-d @", 1)[1].split()[0].strip("\"'")
            if ref not in written:
                return False
        return True


@dataclass
class UsesEnvCredentials(Evaluator[Any, AgentRun]):
    """Agent uses $AGENTSPORE_* env vars instead of hardcoded keys/URLs."""

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        for tc in ctx.output.tool_calls:
            if tc.name != "execute":
                continue
            cmd = tc.args.get("command", "")
            if "agentspore.com" in cmd and "$AGENTSPORE_PLATFORM_URL" not in cmd:
                return False
            if "X-API-Key:" in cmd and "$AGENTSPORE_API_KEY" not in cmd and "af_" in cmd:
                return False
        return True


@dataclass
class HitsExpectedEndpoint(Evaluator[Any, AgentRun]):
    """Agent called the endpoint declared in the case input."""

    endpoint: str = ""

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        target = self.endpoint or (ctx.inputs.get("expected_endpoint") if isinstance(ctx.inputs, dict) else "")
        if not target:
            return True
        for tc in ctx.output.tool_calls:
            if tc.name == "execute" and target in tc.args.get("command", ""):
                return True
        return False
