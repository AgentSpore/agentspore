"""Live Reddit benchmark: real RSS fetch, mocked platform API.

Reddit execute calls run via subprocess (real HTTP to reddit.com).
Platform API calls (/api/v1/*) remain mocked — no prod side-effects.

Run:
    cd agent-runner
    OPENAI_API_KEY=$OPENROUTER_API_KEY \
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    REAL_LLM_MODEL=openai/gpt-oss-120b:free \
    python -m tests.evals.bench_reddit_scout_live
"""
from __future__ import annotations
import asyncio
import json
import subprocess
import sys
import time
from typing import Any

from tests.evals.runner import (
    _build_real_llm_model,
    _trace_from_messages,
    _PROJECTS_MOCK,
    _POST_OK,
    _HEARTBEAT_OK,
)
from tests.evals.cases import REDDIT_SCOUT
from tests.evals.evaluators import (
    AgentRun,
    NoErrors,
    CompletedTask,
    MinExecuteCount,
    ScrapesReddit,
    SendsHeartbeat,
    ChecksDuplicates,
    WriteFileBeforeCurlPost,
    UsesEnvCredentials,
    PostsBlogPost,
    _cmd,
)
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

TIMEOUT = 300


def make_live_stub(projects_get: str = _PROJECTS_MOCK):
    """execute runs real subprocess for reddit.com; mocks everything else."""

    def factory(tool_name: str):
        def fn(**kwargs: Any) -> str:
            if tool_name == "write_file":
                return "ok"
            if tool_name != "execute":
                return f"[stub:{tool_name}] ok"

            flat = " ".join(
                str(v)
                for val in kwargs.values()
                for v in (val if isinstance(val, list) else [val])
            )
            cmd = _cmd(kwargs) or flat

            if "reddit.com" in flat:
                print("  [LIVE] running reddit RSS fetch...", flush=True)
                try:
                    r = subprocess.run(
                        ["bash", "-c", cmd],
                        capture_output=True,
                        text=True,
                        timeout=45,
                    )
                    out = r.stdout.strip()
                    if not out and r.stderr:
                        out = r.stderr.strip()
                    print(f"  [LIVE] reddit returned {len(out)} chars", flush=True)
                    return out or "[]"
                except subprocess.TimeoutExpired:
                    print("  [LIVE] reddit fetch TIMEOUT", flush=True)
                    return "[]"
                except Exception as e:
                    print(f"  [LIVE] reddit fetch ERROR: {e}", flush=True)
                    return "[]"

            if "/api/v1/agents/projects" in flat:
                is_post = "-X POST" in flat or "--request POST" in flat
                return _POST_OK if is_post else projects_get
            if "/api/v1/blog/posts" in flat:
                return _POST_OK
            if "/api/v1/agents/heartbeat" in flat:
                return _HEARTBEAT_OK
            return f"[stub:{tool_name}] ok"

        fn.__name__ = tool_name
        return fn

    return factory


class _Ctx:
    def __init__(self, run: AgentRun) -> None:
        self.output = run
        self.inputs: dict[str, Any] = {}
        self.metrics = None


EVALUATORS = [
    ("NoErr", NoErrors()),
    ("Reddit", ScrapesReddit()),
    ("Heartbt", SendsHeartbeat()),
    ("Dedup", ChecksDuplicates()),
    ("FilePost", WriteFileBeforeCurlPost()),
    ("EnvVars", UsesEnvCredentials()),
    ("Blog", PostsBlogPost()),
    ("MinExec", MinExecuteCount()),
    ("Done", CompletedTask()),
]


async def run_live() -> None:
    print("=== Live Reddit benchmark (real RSS, mock platform) ===\n", flush=True)
    factory = make_live_stub()
    agent: Agent[Any, str] = Agent(
        model=_build_real_llm_model(),
        instructions=REDDIT_SCOUT.system_prompt,
    )
    for t in ("execute", "write_file", "read_file"):
        agent.tool_plain(factory(t), name=t)

    t0 = time.monotonic()
    run: AgentRun
    try:
        result = await asyncio.wait_for(
            agent.run(
                "kick off your scheduled task",
                usage_limits=UsageLimits(request_limit=200),
            ),
            timeout=TIMEOUT,
        )
        trace = _trace_from_messages(result.all_messages())
        run = AgentRun(response=str(result.output), tool_calls=trace)
    except asyncio.TimeoutError:
        run = AgentRun(response="", tool_calls=[], error="TIMEOUT")
    except Exception as exc:
        run = AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")

    elapsed = time.monotonic() - t0
    ctx = _Ctx(run)

    print(f"\n=== {len(run.tool_calls)} tool calls in {elapsed:.0f}s ===")
    for i, tc in enumerate(run.tool_calls):
        preview = _cmd(tc.args) if tc.name == "execute" else str(tc.args)
        print(f"[{i}] {tc.name}: {preview[:100]}")

    print(f"\nResponse (first 500 chars):\n{(run.response or run.error or '')[:500]}")

    print("\n--- Evaluator results ---")
    all_pass = True
    for name, ev in EVALUATORS:
        try:
            score = ev.evaluate(ctx)
        except Exception as e:
            score = f"ERR:{e}"
        icon = "✓" if score is True else "✗"
        if score is not True:
            all_pass = False
        print(f"  {icon} {name}: {score}")

    has_proj = any(
        "-X POST" in str(tc.args) and "/api/v1/agents/projects" in str(tc.args)
        for tc in run.tool_calls
    )
    print(f"\nProject created: {has_proj}")
    print(f"Overall: {'PASS' if all_pass else 'FAIL'}")


asyncio.run(run_live())
