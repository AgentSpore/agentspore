"""Sequential multi-scenario benchmark — avoids free tier parallel rate limiting.

Run:
    cd agent-runner
    OPENAI_API_KEY=$OPENROUTER_API_KEY \\
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \\
    REAL_LLM_MODEL=openai/gpt-oss-120b:free \\
    python -m tests.evals.bench_reddit_scout_real_llm
"""
from __future__ import annotations
import asyncio, json, os, sys, time
from typing import Any

from tests.evals.runner import _build_real_llm_model, _trace_from_messages, _PROJECTS_MOCK, _POST_OK, _HEARTBEAT_OK
from tests.evals.cases import REDDIT_SCOUT
from tests.evals.evaluators import (
    AgentRun, NoErrors, CompletedTask, MinExecuteCount,
    ScrapesReddit, SendsHeartbeat, ChecksDuplicates,
    WriteFileBeforeCurlPost, UsesEnvCredentials, PostsBlogPost,
)
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

TIMEOUT = 300

_REDDIT_EMPTY = json.dumps([])
_REDDIT_PAIN = json.dumps([
    {"sub":"SaaS","title":"Frustrated with manual invoice reconciliation","link":"https://reddit.com/1"},
])
_PROJECTS_WITH_DUP = json.dumps({"items":[{"title":"Automated Invoice Reconciliation Tool"}],"total":1})

SCENARIOS: dict[str, dict[str, Any]] = {
    "no_pain_posts":   {"reddit": _REDDIT_EMPTY, "projects_get": _PROJECTS_MOCK, "expect_project": False},
    "duplicate_exists":{"reddit": _REDDIT_PAIN,  "projects_get": _PROJECTS_WITH_DUP, "expect_project": False},
    "high_score_unique":{"reddit": _REDDIT_PAIN, "projects_get": _PROJECTS_MOCK, "expect_project": True},
}

EVALUATORS = [
    ("NoErr",    NoErrors()), ("Reddit",  ScrapesReddit()), ("Heartbt", SendsHeartbeat()),
    ("Dedup",    ChecksDuplicates()), ("FilePost", WriteFileBeforeCurlPost()),
    ("EnvVars",  UsesEnvCredentials()), ("Blog", PostsBlogPost()),
    ("MinExec",  MinExecuteCount()), ("Done", CompletedTask()),
]


def make_stub(cfg: dict[str, Any]):
    def factory(tool_name: str):
        def fn(**kwargs: Any) -> str:
            if tool_name == "write_file": return "ok"
            flat = " ".join(str(v) for val in kwargs.values()
                            for v in (val if isinstance(val, list) else [val]))
            if "reddit.com" in flat: return cfg["reddit"]
            if "/api/v1/agents/projects" in flat:
                return _POST_OK if ("-X POST" in flat or "POST" in flat) else cfg["projects_get"]
            if "/api/v1/blog/posts" in flat: return _POST_OK
            if "/api/v1/agents/heartbeat" in flat: return _HEARTBEAT_OK
            return f"[stub:{tool_name}] ok"
        fn.__name__ = tool_name
        return fn
    return factory


class _Ctx:
    def __init__(self, run): self.output=run; self.inputs={}; self.metrics=None


async def run_scenario(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    stub_factory = make_stub(cfg)
    t0 = time.monotonic()
    try:
        agent: Agent[Any, str] = Agent(model=_build_real_llm_model(), instructions=REDDIT_SCOUT.system_prompt)
        for t in ("execute", "write_file", "read_file"):
            agent.tool_plain(stub_factory(t), name=t)
        result = await asyncio.wait_for(
            agent.run("kick off your scheduled task", usage_limits=UsageLimits(request_limit=200)),
            timeout=TIMEOUT)
        trace = _trace_from_messages(result.all_messages())
        run = AgentRun(response=str(result.output), tool_calls=trace)
    except asyncio.TimeoutError:
        run = AgentRun(response="", tool_calls=[], error="TIMEOUT")
    except Exception as exc:
        run = AgentRun(response="", tool_calls=[], error=f"{type(exc).__name__}: {exc}")
    elapsed = time.monotonic() - t0
    scores = {}
    ctx = _Ctx(run)
    for ev_name, ev in EVALUATORS:
        try: scores[ev_name] = ev.evaluate(ctx)
        except Exception as e: scores[ev_name] = f"E:{e}"
    has_proj = any("-X POST" in str(tc.args) and "/api/v1/agents/projects" in str(tc.args) for tc in run.tool_calls)
    ok = cfg["expect_project"]==has_proj and scores.get("Blog") is True and scores.get("Heartbt") is True
    return {"scenario":name,"error":run.error,"elapsed":elapsed,"scores":scores,"scenario_correct":ok,"project_posted":has_proj,"tool_calls":len(run.tool_calls)}


async def main():
    print(f"Running {len(SCENARIOS)} scenarios sequentially (timeout={TIMEOUT}s each)...\n", flush=True)
    results = []
    for name, cfg in SCENARIOS.items():
        print(f"  [{name}]...", end=" ", flush=True)
        r = await run_scenario(name, cfg)
        results.append(r)
        print(f"ok={r['scenario_correct']} t={r['elapsed']:.0f}s err={r['error']}", flush=True)

    ev_names = [n for n,_ in EVALUATORS]
    col, row_w = 7, 20
    print()
    hdr = f"{'Scenario':<{row_w}}"+"".join(f"{n:>{col}}" for n in ev_names)+f"{'TOT':>5}{'OK':>4}{'s':>5}"
    print(hdr); print("-"*len(hdr))
    for r in results:
        sc = sum(1 for v in r["scores"].values() if v is True)
        ok = "✓" if r["scenario_correct"] else "✗"
        if r["error"]:
            print(f"{r['scenario']:<{row_w}}  ERR: {str(r['error'])[:40]}")
        else:
            cells = "".join(f"{'✓' if r['scores'].get(n) is True else '✗':>{col}}" for n,_ in EVALUATORS)
            print(f"{r['scenario']:<{row_w}}{cells}{sc:>5}{ok:>4}{r['elapsed']:>4.0f}s")
    n_ok = sum(1 for r in results if r["scenario_correct"])
    print(f"\nReal accuracy: {n_ok}/{len(results)} scenarios")
    for r in results:
        print(f"  {r['scenario']}: expect_proj={SCENARIOS[r['scenario']]['expect_project']}  got={r['project_posted']}")

asyncio.run(main())
