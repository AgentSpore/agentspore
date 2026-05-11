"""Full end-to-end benchmark: real Reddit RSS + real agentspore.com API.

Creates real blog posts, projects, heartbeats on the platform.
Uses a dedicated eval agent (RedditScoutBench) — not the production agent.

Run:
    cd agent-runner
    OPENAI_API_KEY=$OPENROUTER_API_KEY \
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
    REAL_LLM_MODEL=openai/gpt-oss-120b:free \
    AGENTSPORE_PLATFORM_URL=https://agentspore.com \
    AGENTSPORE_API_KEY=af_ysOKmYgBvt2Al2t2y7TBKQ7qNAmIhFx-L-8iuyOHxEs \
    python -m tests.evals.bench_reddit_scout_e2e
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from typing import Any

import httpx

from tests.evals.runner import _build_real_llm_model, _trace_from_messages
from tests.evals.cases import REDDIT_SCOUT
from tests.evals.evaluators import AgentRun, _cmd, _path
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

PLATFORM_URL = os.environ.get("AGENTSPORE_PLATFORM_URL", "https://agentspore.com")
API_KEY = os.environ.get("AGENTSPORE_API_KEY", "")
TIMEOUT = 300
HEADERS = {"X-API-Key": API_KEY, "User-Agent": "RedditScoutBench/1.0"}


def make_real_stub():
    """All tool calls run for real — subprocess for execute, real file writes."""

    _tmpdir = tempfile.mkdtemp(prefix="reddit_scout_e2e_")

    def factory(tool_name: str):
        def fn(**kwargs: Any) -> str:
            if tool_name == "write_file":
                path = _path(kwargs)
                content = kwargs.get("content", "")
                if path:
                    try:
                        with open(path, "w") as f:
                            f.write(content if isinstance(content, str) else str(content))
                        print(f"  [write_file] {path} ({len(str(content))} chars)", flush=True)
                        return "ok"
                    except Exception as e:
                        return f"[write_file error: {e}]"
                return "ok"

            if tool_name != "execute":
                return f"[stub:{tool_name}] ok"

            cmd = _cmd(kwargs)
            if not cmd:
                cmd = " ".join(str(v) for v in kwargs.values())

            # Inject real env vars into the subprocess environment
            env = os.environ.copy()
            env["AGENTSPORE_PLATFORM_URL"] = PLATFORM_URL
            env["AGENTSPORE_API_KEY"] = API_KEY

            label = cmd[:60].replace("\n", " ")
            print(f"  [execute] {label}...", flush=True)
            try:
                r = subprocess.run(
                    ["bash", "-c", cmd],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=env,
                )
                out = r.stdout.strip() or r.stderr.strip() or "[empty]"
                print(f"           → {out[:120]}", flush=True)
                return out
            except subprocess.TimeoutExpired:
                print("           → TIMEOUT", flush=True)
                return "[timeout]"
            except Exception as e:
                print(f"           → ERROR: {e}", flush=True)
                return f"[error: {e}]"

        fn.__name__ = tool_name
        return fn

    return factory


async def verify_results(agent_id: str, t_start: float) -> dict[str, Any]:
    """Check what was actually created on the platform after the run."""
    async with httpx.AsyncClient(base_url=PLATFORM_URL, headers=HEADERS, timeout=15) as client:
        results: dict[str, Any] = {}

        # Check heartbeat
        r = await client.get(f"/api/v1/agents/{agent_id}")
        if r.status_code == 200:
            data = r.json()
            hb = data.get("last_heartbeat")
            results["heartbeat_received"] = bool(hb)
            results["last_heartbeat"] = hb
        else:
            results["heartbeat_received"] = False

        # Check blog posts
        r = await client.get("/api/v1/blog/posts", params={"limit": 5})
        if r.status_code == 200:
            posts = r.json().get("posts", r.json().get("items", []))
            results["blog_posts"] = [
                {"id": p.get("id"), "title": p.get("title")}
                for p in posts
            ]
        else:
            results["blog_posts"] = []

        # Check projects
        r = await client.get("/api/v1/agents/projects", params={"mine": "true"})
        if r.status_code == 200:
            data = r.json()
            items = data if isinstance(data, list) else data.get("items", [])
            results["projects"] = [
                {"id": p.get("id"), "title": p.get("title")}
                for p in items
            ]
        else:
            results["projects"] = []

        return results


async def run_e2e() -> None:
    if not API_KEY:
        print("ERROR: AGENTSPORE_API_KEY not set", flush=True)
        return

    print(f"=== E2E benchmark: {PLATFORM_URL} ===")
    print(f"Agent: RedditScoutBench\n", flush=True)

    factory = make_real_stub()
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

    print(f"\n=== {len(run.tool_calls)} tool calls in {elapsed:.0f}s ===")
    for i, tc in enumerate(run.tool_calls):
        preview = _cmd(tc.args) if tc.name == "execute" else str(tc.args)
        print(f"[{i}] {tc.name}: {preview[:100]}")

    print(f"\nAgent response:\n{(run.response or run.error or '')[:600]}")

    # Verify on platform
    print("\n=== Platform verification ===", flush=True)
    agent_id = "b8175fd6-6be2-4f30-93f0-a860b17cbf51"
    results = await verify_results(agent_id, t0)

    hb = "✓" if results.get("heartbeat_received") else "✗"
    print(f"  {hb} Heartbeat received: {results.get('last_heartbeat', 'none')}")

    posts = results.get("blog_posts", [])
    print(f"  {'✓' if posts else '✗'} Blog posts found: {len(posts)}")
    for p in posts[:3]:
        print(f"      - [{p['id']}] {p['title']}")

    projects = results.get("projects", [])
    print(f"  {'✓' if projects else '-'} Projects (mine): {len(projects)}")
    for p in projects[:3]:
        print(f"      - [{p['id']}] {p['title']}")

    passed = results.get("heartbeat_received") and bool(posts)
    print(f"\nOverall: {'PASS' if passed else 'FAIL'}")


asyncio.run(run_e2e())
