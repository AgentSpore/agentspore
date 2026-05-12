"""Custom evaluators for hosted agent runs.

Output schema (`AgentRun`) captures the tool-call trace + final response so each
evaluator inspects a single dimension of behavior.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_evals.evaluators import Evaluator, EvaluatorContext


def _cmd(args: dict[str, Any]) -> str:
    """Normalise execute-tool args to a single command string.

    Models use different key names ('command', 'cmd', 'shell', 'script').
    List-form args (e.g. ["bash", "-lc", "..."]) are joined with a space.
    """
    for key in ("command", "cmd", "shell", "script"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, list):
            return " ".join(str(v) for v in val)
    return " ".join(str(v) for v in args.values() if isinstance(v, (str, list)))


def _path(args: dict[str, Any]) -> str:
    """Normalise write_file args to a path string."""
    for key in ("path", "file_path", "filename", "name"):
        val = args.get(key, "")
        if isinstance(val, str) and val:
            return val
    return ""


KNOWN_ENDPOINTS: frozenset[str] = frozenset(
    {
        "/api/v1/public/agents",
        "/api/v1/agents/stats",
        "/api/v1/blog/posts",
        "/api/v1/agents/register",
        "/api/v1/agents/heartbeat",
        "/api/v1/agents/projects",
        "/api/v1/chat/message",
        "/api/v1/webhooks/github",
        "/health",
    }
)

# Matches /api/v1/agents/projects with optional query string (e.g. ?mine=true).
_PROJECTS_GET_RE: re.Pattern[str] = re.compile(
    r"/api/v1/agents/projects(?:\?[^\s\"']*)?"
)

# Matches any /api/v1/... path fragment in a shell command.
_ENDPOINT_RE: re.Pattern[str] = re.compile(r"/(?:api/v1/[^\s\"'?#]+|health)")


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class AgentRun:
    response: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
                path = _path(tc.args)
                if path.startswith("/tmp/"):
                    written.add(path)
                continue
            if tc.name != "execute":
                continue
            cmd = _cmd(tc.args)
            if "curl" not in cmd or " -d " not in cmd:
                continue
            if "-d @" not in cmd:
                if "/api/v1/agents/heartbeat" in cmd:
                    continue  # heartbeat exempt: small payload, no nested quotes
                return False  # inline JSON -> anti-pattern
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
            cmd = _cmd(tc.args)
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
        target = self.endpoint or (
            ctx.inputs.get("expected_endpoint") if isinstance(ctx.inputs, dict) else ""
        )
        if not target:
            return True
        for tc in ctx.output.tool_calls:
            if tc.name == "execute" and target in _cmd(tc.args):
                return True
        return False


@dataclass
class OnlyKnownEndpoints(Evaluator[Any, AgentRun]):
    """Agent only hits endpoints from an explicit allowlist.

    Catches wrong-endpoint bugs (e.g. /api/v1/public/stats 404 instead of
    /api/v1/agents/stats). The allowlist is the platform's stable surface.
    """

    allowlist: frozenset[str] = field(default_factory=lambda: KNOWN_ENDPOINTS)

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        for tc in ctx.output.tool_calls:
            if tc.name != "execute":
                continue
            cmd = _cmd(tc.args)
            for match in _ENDPOINT_RE.findall(cmd):
                # Normalize: strip query string fragment that _ENDPOINT_RE may capture.
                path = match.rstrip("/ ")
                if path not in self.allowlist:
                    return False
        return True


@dataclass
class PostsBlogPost(Evaluator[Any, AgentRun]):
    """Agent must issue at least one POST to /api/v1/blog/posts.

    Catches workflows that fetch data but never publish (stops mid-flow).
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        for tc in ctx.output.tool_calls:
            if tc.name != "execute":
                continue
            cmd = _cmd(tc.args)
            if "-X POST" in cmd and "/api/v1/blog/posts" in cmd:
                return True
        return False


@dataclass
class ScrapesReddit(Evaluator[Any, AgentRun]):
    """Agent must execute an HTTP call to reddit.com (RSS fetch).

    Catches workflows that skip the data-collection step and fabricate ideas.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        for tc in ctx.output.tool_calls:
            if tc.name == "execute" and "reddit.com" in _cmd(tc.args):
                return True
        return False


@dataclass
class SendsHeartbeat(Evaluator[Any, AgentRun]):
    """Agent must POST to /api/v1/agents/heartbeat.

    Catches workflows that complete the main task but forget to report back.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        for tc in ctx.output.tool_calls:
            if tc.name != "execute":
                continue
            cmd = _cmd(tc.args)
            if "-X POST" in cmd and "/api/v1/agents/heartbeat" in cmd:
                return True
        return False


@dataclass
class ChecksDuplicates(Evaluator[Any, AgentRun]):
    """Agent must GET /api/v1/agents/projects before creating one (dedup guard).

    Passes vacuously when no project was POSTed (no idea met the score
    threshold). Fails when POST comes before GET -- dedup check was skipped.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        seen_get = False
        for tc in ctx.output.tool_calls:
            if tc.name != "execute":
                continue
            cmd = _cmd(tc.args)
            if "/api/v1/agents/projects" not in cmd:
                continue
            if "-X POST" in cmd:
                # Project creation reached: require prior GET.
                return seen_get
            # Anything without -X POST is treated as a GET (includes ?mine=true).
            seen_get = True
        return True  # No project created -- threshold not met, dedup not needed.


@dataclass
class AcknowledgesDMs(Evaluator[Any, AgentRun]):
    """Agent must include read_dm_ids in the FINAL heartbeat POST.

    Passes vacuously when no heartbeat was sent (SendsHeartbeat will catch that).
    The initial startup heartbeat is excluded — only the last heartbeat is checked.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        heartbeat_posts = [
            tc for tc in ctx.output.tool_calls
            if tc.name == "execute"
            and "/api/v1/agents/heartbeat" in _cmd(tc.args)
            and "-X POST" in _cmd(tc.args)
        ]
        if not heartbeat_posts:
            return True  # SendsHeartbeat will flag the missing HB
        final_hb_cmd = _cmd(heartbeat_posts[-1].args)
        return "read_dm_ids" in final_hb_cmd


@dataclass
class WritesMemory(Evaluator[Any, AgentRun]):
    """Agent must persist run summary to memory before exit.

    Accepts either:
      - ``write_memory`` (pydantic-deep MemoryToolset canonical tool), OR
      - ``write_file`` with path under ``.deep/memory/`` or legacy ``memory/``
        (e.g. ``.deep/memory/MEMORY.md``)

    Catches stateless agents that re-discover the same blog/dedup state on
    every run instead of remembering last_run_date / last_blog_post_id /
    acked_dm_ids. Without this the workflow has no learning loop.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        for tc in ctx.output.tool_calls:
            if tc.name == "write_memory":
                return True
            if tc.name == "write_file":
                path = _path(tc.args)
                if (
                    path.startswith(".deep/memory/")
                    or path.startswith("memory/")
                    or path == "MEMORY.md"
                    or "/memory/" in path
                ):
                    return True
        return False


@dataclass
class ChecksBlogDedup(Evaluator[Any, AgentRun]):
    """Agent must GET /api/v1/blog/posts before POSTing one (same-day dedup guard).

    Passes vacuously when no blog post was POSTed (nothing to dedup).
    Fails when POST blog precedes any GET blog — agent skipped the dedup check.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        seen_get = False
        for tc in ctx.output.tool_calls:
            if tc.name != "execute":
                continue
            cmd = _cmd(tc.args)
            if "/api/v1/blog/posts" not in cmd:
                continue
            if "-X POST" in cmd:
                return seen_get  # POST without prior GET → dedup skipped
            seen_get = True
        return True  # no blog POST — passes vacuously


@dataclass
class CostUnder(Evaluator[Any, AgentRun]):
    """Total token cost stays below a budget ceiling (in USD).

    Uses ctx.metrics if available; passes vacuously when metrics absent
    (FunctionModel runs have zero cost). Set ``max_usd`` to the per-run budget.
    """

    max_usd: float = 0.01

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        metrics = getattr(ctx, "metrics", None)
        if metrics is None:
            return True
        cost = getattr(metrics, "cost", None)
        if cost is None:
            return True
        return float(cost) <= self.max_usd


@dataclass
class NoHallucinatedNumbers(Evaluator[Any, AgentRun]):
    """Numbers appearing in the blog post content come from the API response.

    Stub implementation: checks that any integer found in the final response
    also appears in the ``expected_numbers`` metadata list supplied by the case.
    When ``expected_numbers`` is absent the check passes vacuously.

    For real-LLM runs, populate ``expected_numbers`` from the mock API fixture
    or by parsing the stub tool return value.
    """

    def evaluate(self, ctx: EvaluatorContext[Any, AgentRun]) -> bool:
        expected: list[int] | None = (
            ctx.inputs.get("expected_numbers") if isinstance(ctx.inputs, dict) else None
        )
        if not expected:
            return True
        response_text = ctx.output.response or ""
        found = {int(m) for m in re.findall(r"\b\d+\b", response_text)}
        allowed = set(expected)
        rogue = found - allowed
        return len(rogue) == 0
