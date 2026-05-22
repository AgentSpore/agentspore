"""Iter9 — per-agent best-model search over live OpenRouter free models.

Runs two workloads (Scout, Builder) per candidate model, capturing
elapsed time, tool-call count, evaluator scores, and error type.

Scout workload: REDDIT_SCOUT high_score_unique scenario (rich + dedup +
heartbeat + blog publish). 11 evaluators -> max score 11.

Builder workload: synthetic multi-file scaffold. Prompts the agent to
write at least 3 source files into /tmp/proj/ and run a curl POST to
register the project. 8 evaluator checks -> max score 8. This is a
lighter proxy than full bench_multiagent Docker spawn (which would
exceed free-tier rpm).

Output: /tmp/iter9_model_matrix.json

Run:
    cd agent-runner
    OPENROUTER_API_KEY=... uv run python -m tests.evals.iter9_model_sweep
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from tests.evals.cases import REDDIT_SCOUT
from tests.evals.evaluators import (
    AcknowledgesDMs,
    AgentRun,
    ChecksBlogDedup,
    ChecksDuplicates,
    CompletedTask,
    MinExecuteCount,
    NoErrors,
    PostsBlogPost,
    ScrapesReddit,
    SendsHeartbeat,
    UsesEnvCredentials,
    WriteFileBeforeCurlPost,
    _cmd,
    _path,
)
from tests.evals.runner import _trace_from_messages

_ALL_MODELS: list[str] = [
    "arcee-ai/trinity-large-thinking:free",
    "z-ai/glm-4.5-air:free",
    "qwen/qwen3-coder:free",
    "openai/gpt-oss-120b:free",
    "deepseek/deepseek-v4-flash:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "minimax/minimax-m2.5:free",
]

# Allow MODELS to be filtered via env (comma-separated indices or names) so
# the sweep can be batched across multiple Bash calls (each call <10min).
def _filter_models() -> list[str]:
    raw = os.environ.get("ITER9_MODELS", "").strip()
    if not raw:
        return list(_ALL_MODELS)
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if all(x.isdigit() for x in items):
        return [_ALL_MODELS[int(i)] for i in items if int(i) < len(_ALL_MODELS)]
    return items

MODELS: list[str] = _filter_models()

PER_RUN_TIMEOUT = int(os.environ.get("ITER9_TIMEOUT", "120"))
INTER_MODEL_SLEEP = int(os.environ.get("ITER9_MODEL_SLEEP", "20"))
INTER_RUN_SLEEP = int(os.environ.get("ITER9_RUN_SLEEP", "15"))

# Same fixtures as bench_reddit_scout_real_llm "high_score_unique".
_REDDIT_PAIN = json.dumps([
    {"sub": "SaaS", "title": "Frustrated with manual invoice reconciliation",
     "link": "https://reddit.com/1"},
])
_POST_OK = json.dumps({"id": "mock-id-123", "status": "created"})
_PROJECTS_EMPTY = json.dumps({"items": [], "total": 0})
_BLOG_EMPTY = json.dumps({"posts": [], "total": 0})
_HEARTBEAT_OK = json.dumps({
    "status": "ok", "received": True, "session_id": "sess-iter9",
    "direct_messages": [
        {"id": "dm-1", "from": "@adminagentspore",
         "content": "What pain points today?"},
    ],
})


def scout_stub_factory(name: str):
    def fn(**kwargs: Any) -> str:
        if name == "write_file":
            return "ok"
        flat = " ".join(str(v) for val in kwargs.values()
                        for v in (val if isinstance(val, list) else [val]))
        if "reddit.com" in flat:
            return _REDDIT_PAIN
        if "/api/v1/agents/projects" in flat:
            return _POST_OK if ("-X POST" in flat or "POST" in flat) else _PROJECTS_EMPTY
        if "/api/v1/blog/posts" in flat:
            return _POST_OK if ("-X POST" in flat or "--request POST" in flat) else _BLOG_EMPTY
        if "/api/v1/agents/heartbeat" in flat:
            return _HEARTBEAT_OK
        return f"[stub:{name}] ok"
    fn.__name__ = name
    return fn


def builder_stub_factory(name: str):
    """Builder stubs — write_file accepts everything, execute returns shell-ish output."""
    def fn(**kwargs: Any) -> str:
        if name == "write_file":
            path = _path(kwargs) or "<unknown>"
            return f"wrote {path} ({len(str(kwargs.get('content', '')))} bytes)"
        if name == "read_file":
            return "(file body elided)"
        if name == "execute":
            cmd = _cmd(kwargs)
            if "ls" in cmd and "/tmp/proj" in cmd:
                return "main.py\nrequirements.txt\nREADME.md"
            if "curl" in cmd and "/api/v1/agents/projects" in cmd:
                return _POST_OK
            if "curl" in cmd and "/api/v1/agents/heartbeat" in cmd:
                return _HEARTBEAT_OK
            if "python" in cmd or "pytest" in cmd:
                return "== 0 errors, 0 warnings =="
            return "ok"
        return f"[stub:{name}] ok"
    fn.__name__ = name
    return fn


class _Ctx:
    def __init__(self, run: AgentRun) -> None:
        self.output = run
        self.inputs: dict[str, Any] = {}
        self.metrics = None


SCOUT_EVALUATORS = [
    ("NoErr", NoErrors()),
    ("Done", CompletedTask()),
    ("MinExec", MinExecuteCount()),
    ("Reddit", ScrapesReddit()),
    ("Heartbt", SendsHeartbeat()),
    ("Dedup", ChecksDuplicates()),
    ("FilePost", WriteFileBeforeCurlPost()),
    ("EnvVars", UsesEnvCredentials()),
    ("Blog", PostsBlogPost()),
    ("DMAck", AcknowledgesDMs()),
    ("BlogDedup", ChecksBlogDedup()),
]


# Builder-workload evaluators (synthetic, derived from trace).
def builder_scores(run: AgentRun) -> dict[str, bool]:
    """Eight checks for builder workload."""
    writes = [tc for tc in run.tool_calls if tc.name == "write_file"]
    execs = [tc for tc in run.tool_calls if tc.name == "execute"]
    paths = {_path(tc.args) for tc in writes if _path(tc.args)}
    exec_cmds = [_cmd(tc.args) for tc in execs]

    has_main = any("main" in p for p in paths)
    has_req = any("requirements" in p or "pyproject" in p for p in paths)
    has_readme = any("readme" in p.lower() for p in paths)
    multi_file = len(paths) >= 3
    used_tmpdir = any(p.startswith("/tmp/") or p.startswith("/workspace") for p in paths)
    ran_curl_post = any("-X POST" in c and "/api/v1/agents/projects" in c for c in exec_cmds)
    used_env = all(
        not ("agentspore.com" in c and "$AGENTSPORE_PLATFORM_URL" not in c)
        for c in exec_cmds
    )
    no_error = run.error is None
    return {
        "NoErr": no_error,
        "MultiFile": multi_file,
        "Main": has_main,
        "Req": has_req,
        "Readme": has_readme,
        "TmpDir": used_tmpdir,
        "RegPost": ran_curl_post,
        "EnvVars": used_env,
    }


BUILDER_SYSTEM_PROMPT = (
    "You are BuilderAgent. Given a project brief, you scaffold a minimal "
    "Python project under /tmp/proj/ with at least: main.py, requirements.txt, "
    "README.md. Then register the project via:\n"
    "  curl -X POST $AGENTSPORE_PLATFORM_URL/api/v1/agents/projects "
    "-H 'X-API-Key: $AGENTSPORE_API_KEY' -d @/tmp/proj.json\n"
    "Use write_file then execute; do NOT inline JSON in curl. Stop when "
    "project is registered. Brief: build an invoice reconciliation tool."
)


async def run_scout(model: str) -> dict[str, Any]:
    t0 = time.monotonic()
    error: str | None = None
    try:
        agent: Agent[Any, str] = Agent(
            model=f"openai:{model}",
            instructions=REDDIT_SCOUT.system_prompt,
        )
        for t in ("execute", "write_file", "read_file"):
            agent.tool_plain(scout_stub_factory(t), name=t)
        result = await asyncio.wait_for(
            agent.run("kick off your scheduled task",
                      usage_limits=UsageLimits(request_limit=200)),
            timeout=PER_RUN_TIMEOUT,
        )
        trace = _trace_from_messages(result.all_messages())
        run = AgentRun(response=str(result.output), tool_calls=trace)
    except asyncio.TimeoutError:
        run = AgentRun(response="", tool_calls=[], error="TIMEOUT")
        error = "TIMEOUT"
    except Exception as exc:
        run = AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")
        error = type(exc).__name__
    elapsed = time.monotonic() - t0
    ctx = _Ctx(run)
    scores: dict[str, Any] = {}
    for name, ev in SCOUT_EVALUATORS:
        try:
            scores[name] = bool(ev.evaluate(ctx))
        except Exception as e:
            scores[name] = f"E:{type(e).__name__}"
    pass_count = sum(1 for v in scores.values() if v is True)
    return {
        "workload": "RS_SCOUT",
        "model": model,
        "elapsed": round(elapsed, 1),
        "tool_calls": len(run.tool_calls),
        "score": pass_count,
        "max_score": len(SCOUT_EVALUATORS),
        "scores": scores,
        "error": error or run.error,
    }


async def run_builder(model: str) -> dict[str, Any]:
    t0 = time.monotonic()
    error: str | None = None
    try:
        agent: Agent[Any, str] = Agent(
            model=f"openai:{model}",
            instructions=BUILDER_SYSTEM_PROMPT,
        )
        for t in ("execute", "write_file", "read_file"):
            agent.tool_plain(builder_stub_factory(t), name=t)
        result = await asyncio.wait_for(
            agent.run(
                "Scaffold the invoice reconciliation tool and register the project.",
                usage_limits=UsageLimits(request_limit=200),
            ),
            timeout=PER_RUN_TIMEOUT,
        )
        trace = _trace_from_messages(result.all_messages())
        run = AgentRun(response=str(result.output), tool_calls=trace)
    except asyncio.TimeoutError:
        run = AgentRun(response="", tool_calls=[], error="TIMEOUT")
        error = "TIMEOUT"
    except Exception as exc:
        run = AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")
        error = type(exc).__name__
    elapsed = time.monotonic() - t0
    scores = builder_scores(run)
    pass_count = sum(1 for v in scores.values() if v is True)
    return {
        "workload": "RS_BUILDER",
        "model": model,
        "elapsed": round(elapsed, 1),
        "tool_calls": len(run.tool_calls),
        "score": pass_count,
        "max_score": len(scores),
        "scores": scores,
        "error": error or run.error,
    }


async def main() -> None:
    out_path = "/tmp/iter9_model_matrix.json"
    results: list[dict[str, Any]] = []
    # Append to prior batch results if file exists (multi-batch runs).
    if os.environ.get("ITER9_APPEND", "1") == "1" and os.path.exists(out_path):
        try:
            with open(out_path) as f:
                prior = json.load(f)
            if isinstance(prior, list):
                results = prior
                print(f"[append] loaded {len(prior)} prior results", flush=True)
        except Exception as e:
            print(f"[append] could not load prior results: {e}", flush=True)
    print(f"Iter9 sweep over {len(MODELS)} models × 2 workloads", flush=True)
    print(f"per-run timeout={PER_RUN_TIMEOUT}s, inter-run sleep={INTER_RUN_SLEEP}s, "
          f"inter-model sleep={INTER_MODEL_SLEEP}s\n", flush=True)
    for idx, model in enumerate(MODELS):
        print(f"--- [{idx+1}/{len(MODELS)}] {model}", flush=True)
        scout = await run_scout(model)
        print(f"   scout : score={scout['score']}/{scout['max_score']} "
              f"tools={scout['tool_calls']} t={scout['elapsed']}s err={scout['error']}",
              flush=True)
        results.append(scout)
        # save after each run so a hang doesn't lose data
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        await asyncio.sleep(INTER_RUN_SLEEP)
        builder = await run_builder(model)
        print(f"   build : score={builder['score']}/{builder['max_score']} "
              f"tools={builder['tool_calls']} t={builder['elapsed']}s err={builder['error']}",
              flush=True)
        results.append(builder)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        if idx < len(MODELS) - 1:
            print(f"   ...sleep {INTER_MODEL_SLEEP}s before next model", flush=True)
            await asyncio.sleep(INTER_MODEL_SLEEP)

    print(f"\nWrote {out_path} with {len(results)} runs", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
