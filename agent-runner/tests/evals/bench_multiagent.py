"""Multi-agent pipeline eval: RedditScout → QAAgent → ContentAgent.

Tests the full A2A coordination locally without the platform.
Phase 1: RedditScoutAgent — scouts Reddit, creates project, spawns MVP subagent,
         pushes code, DMs qaagent + contentagent.
Phase 2: QAAgent — receives DM from Phase 1, spawns test-writer subagent,
         pushes tests, replies to RedditScout.

Run:
    cd agent-runner
    OPENAI_API_KEY=$OPENROUTER_API_KEY \\
    OPENAI_BASE_URL=https://openrouter.ai/api/v1 \\
    REAL_LLM_MODEL=nvidia/nemotron-3-super-120b-a12b:free \\
    uv run python -m tests.evals.bench_multiagent
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from tests.evals.cases import REDDIT_SCOUT, QA_AGENT
from tests.evals.runner import _build_real_llm_model, _trace_from_messages
from tests.evals.evaluators import AgentRun

PHASE_TIMEOUT = 900  # seconds per agent phase (nemotron free: ~50s/call, ~18 calls)
SUBAGENT_TIMEOUT = 240  # seconds per subagent call

# ── Shared pipeline state ──────────────────────────────────────────────────────

class PipelineState:
    """Mutable state shared across multi-agent pipeline phases."""

    def __init__(self):
        self.task_store: dict[str, str] = {}
        self.dm_inbox: dict[str, list[dict]] = {}  # handle → messages
        self.project_created: dict | None = None
        self.files_pushed: list[str] = []
        self.tests_pushed: list[str] = []

    def capture_dm(self, target_handle: str, content: str, sender: str = "unknown"):
        msgs = self.dm_inbox.setdefault(target_handle, [])
        msgs.append({
            "id": f"dm-{uuid.uuid4().hex[:8]}",
            "from": sender,
            "from_name": sender,
            "content": content,
        })

    def inbox_for(self, handle: str) -> list[dict]:
        return self.dm_inbox.get(handle, [])


# ── Stub helpers ──────────────────────────────────────────────────────────────

def _run_safe_local(cmd: str) -> str:
    """Execute safe local commands (mkdir, python3, find) for real file ops."""
    safe_prefixes = ("mkdir", "python3 -c", "python3 -m", "find /tmp", "ls /tmp", "cat /tmp")
    if any(cmd.strip().startswith(p) for p in safe_prefixes):
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            return (r.stdout or "") + (r.stderr or "")
        except Exception as e:
            return f"ERROR: {e}"
    return None  # not a safe local command — caller should mock


def _write_to_fs(path: str, content: str) -> None:
    """Write content to filesystem if path is under /tmp/."""
    if not path or not path.startswith("/tmp/"):
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content))
    except Exception:
        pass


def _make_task_stub(state: PipelineState, model: str, role: str, extra_stubs: dict) -> Any:
    """Return an async task stub that runs a real LLM subagent."""

    async def task(**kwargs: Any) -> str:  # noqa: ANN401
        instructions = str(kwargs.get("instructions", kwargs.get("description", kwargs)))
        task_id = f"task-{uuid.uuid4().hex[:8]}"

        sub = Agent(
            model=model,
            instructions=f"You are a {role}. Follow all instructions precisely. Write complete, working code.",
        )
        for name, fn in extra_stubs.items():
            sub.tool_plain(fn, name=name)

        try:
            result = await asyncio.wait_for(
                sub.run(instructions, usage_limits=UsageLimits(request_limit=40)),
                timeout=SUBAGENT_TIMEOUT,
            )
            output = str(result.output)
        except asyncio.TimeoutError:
            output = f"TIMEOUT after {SUBAGENT_TIMEOUT}s"
        except Exception as e:
            output = f"ERROR: {e}"

        state.task_store[task_id] = output
        return json.dumps({"task_id": task_id, "status": "running"})

    return task


def _wait_tasks_stub(state: PipelineState) -> Any:
    def wait_tasks(**kwargs: Any) -> str:  # noqa: ANN401
        results = [
            {"task_id": k, "status": "completed", "output": v}
            for k, v in state.task_store.items()
        ]
        state.task_store.clear()
        return json.dumps({"tasks": results, "all_complete": True})
    return wait_tasks


# ── RedditScout stubs ──────────────────────────────────────────────────────────

_REDDIT_PAIN = json.dumps([
    {"sub": "SaaS", "title": "No tool auto-detects when your REST API docs drift from actual endpoints — we find out only when users report 404s. Checked ProductHunt/GitHub, nothing does this end-to-end", "link": "https://reddit.com/r/SaaS/1"},
    {"sub": "startups", "title": "Wish there was a FastAPI app that monitors my OpenAPI spec vs live routes and sends Slack alerts on drift — this niche has zero competition", "link": "https://reddit.com/r/startups/2"},
    {"sub": "webdev", "title": "How do I build a microservice that diffs swagger.json against running FastAPI routes? Spent 2 days looking, no open-source solution exists", "link": "https://reddit.com/r/webdev/3"},
])
_PROJECTS_EMPTY = json.dumps({"items": [], "total": 0})
_PROJECT_CREATED = json.dumps({
    "id": "proj-abc-123",
    "title": "Invoice Reconciliation Tool",
    "repo_url": "https://github.com/AgentSpore/invoice-reconciliation-tool",
    "status": "building",
})
_BLOG_EMPTY = json.dumps({"posts": [], "total": 0})
_BLOG_OK = json.dumps({"id": "blog-xyz-456", "status": "created"})
_HB_OK = json.dumps({"status": "ok", "session_id": "sess-rs-001", "direct_messages": []})
_PUSH_OK = json.dumps({"pushed": 3, "commit": "abc123def", "status": "ok"})
_DM_OK = json.dumps({"status": "sent", "dm_id": "dm-sent-001"})


def make_reddit_scout_stubs(state: PipelineState, model: str) -> dict[str, Any]:
    def write_file(**kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        content = kwargs.get("content", "")
        if isinstance(content, dict):
            content = json.dumps(content, indent=2)
        _write_to_fs(path, content)

        # Capture DM file content
        if "qa_dm" in path:
            try:
                dm_data = json.loads(content)
                state.capture_dm("qaagent", dm_data.get("content", content), "redditscouthosted")
            except Exception:
                state.capture_dm("qaagent", content, "redditscouthosted")
        elif "content_dm" in path:
            try:
                dm_data = json.loads(content)
                state.capture_dm("contentagent", dm_data.get("content", content), "redditscouthosted")
            except Exception:
                state.capture_dm("contentagent", content, "redditscouthosted")
        return "ok"

    def read_file(**kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        if os.path.exists(path):
            try:
                return open(path).read()
            except Exception:
                pass
        return f"[not found: {path}]"

    def list_files(**kwargs: Any) -> str:
        return json.dumps([])

    def execute(**kwargs: Any) -> str:
        cmd = str(kwargs.get("command", ""))

        # Mock reddit.com BEFORE _run_safe_local — Step 1 script hits reddit.com
        # via urllib which blocks for 30s (timeout) before returning empty.
        if "reddit.com" in cmd:
            return _REDDIT_PAIN

        # Run safe local commands for real file I/O
        local = _run_safe_local(cmd)
        if local is not None:
            return local

        # Mock curl calls
        if "/dm/qaagent" in cmd and "POST" in cmd:
            # Also try to read the dm file if written separately
            state.capture_dm("qaagent", "[DM via execute curl to /dm/qaagent]", "redditscouthosted")
            return _DM_OK
        if "/dm/contentagent" in cmd and "POST" in cmd:
            state.capture_dm("contentagent", "[DM via execute curl to /dm/contentagent]", "redditscouthosted")
            return _DM_OK
        if "/dm/" in cmd and "POST" in cmd:
            return _DM_OK
        if "/push" in cmd and "POST" in cmd:
            # Read actual push.json to capture file list
            for pf in ("/tmp/push.json",):
                if os.path.exists(pf):
                    try:
                        data = json.load(open(pf))
                        state.files_pushed = [fi["path"] for fi in data.get("files", [])]
                    except Exception:
                        pass
            return _PUSH_OK
        if "reddit.com" in cmd:
            return _REDDIT_PAIN
        if "/api/v1/agents/projects" in cmd and "POST" in cmd and "push" not in cmd:
            state.project_created = json.loads(_PROJECT_CREATED)
            return _PROJECT_CREATED
        if "/api/v1/agents/projects" in cmd:
            return _PROJECTS_EMPTY
        if "/api/v1/blog/posts" in cmd and "POST" in cmd:
            return _BLOG_OK
        if "/api/v1/blog/posts" in cmd:
            return _BLOG_EMPTY
        if "/api/v1/agents/heartbeat" in cmd:
            return _HB_OK
        return "[stub:execute] ok"

    # Subagent stubs (used inside task subagent)
    sub_stubs = {
        "execute": execute,
        "write_file": write_file,
        "read_file": read_file,
        "list_files": list_files,
    }
    task = _make_task_stub(state, model, "MVP builder — write complete FastAPI code", sub_stubs)
    wait_tasks = _wait_tasks_stub(state)

    return {
        "execute": execute,
        "write_file": write_file,
        "read_file": read_file,
        "list_files": list_files,
        "task": task,
        "wait_tasks": wait_tasks,
        "list_active_tasks": lambda **kw: json.dumps({"tasks": []}),
        "check_task": lambda **kw: json.dumps({"status": "completed"}),
    }


# ── QAAgent stubs ──────────────────────────────────────────────────────────────

def make_qa_agent_stubs(
    state: PipelineState, model: str, dm_inbox: list[dict]
) -> dict[str, Any]:
    _HB_WITH_DM = json.dumps({
        "status": "ok",
        "session_id": "sess-qa-001",
        "direct_messages": dm_inbox,
    })
    _HB_FINAL = json.dumps({"status": "ok", "session_id": "sess-qa-002", "direct_messages": []})

    hb_call_count = {"n": 0}

    def write_file(**kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        content = kwargs.get("content", "")
        if isinstance(content, dict):
            content = json.dumps(content, indent=2)
        _write_to_fs(path, content)
        return "ok"

    def read_file(**kwargs: Any) -> str:
        path = str(kwargs.get("path", ""))
        if os.path.exists(path):
            try:
                return open(path).read()
            except Exception:
                pass
        return f"[not found: {path}]"

    def execute(**kwargs: Any) -> str:
        cmd = str(kwargs.get("command", ""))

        if "reddit.com" in cmd:
            return _REDDIT_PAIN

        local = _run_safe_local(cmd)
        if local is not None:
            return local

        if "/api/v1/agents/heartbeat" in cmd:
            hb_call_count["n"] += 1
            if hb_call_count["n"] == 1:
                return _HB_WITH_DM
            return _HB_FINAL
        if "/push" in cmd and "POST" in cmd:
            for pf in ("/tmp/qa_push.json",):
                if os.path.exists(pf):
                    try:
                        data = json.load(open(pf))
                        state.tests_pushed = [fi["path"] for fi in data.get("files", [])]
                    except Exception:
                        pass
            return _PUSH_OK
        if "/dm/" in cmd and "POST" in cmd:
            return _DM_OK
        if "/api/v1/agents/stats" in cmd or "/health" in cmd.split("?")[0]:
            return "200"
        if "/api/v1/blog/posts" in cmd and "POST" in cmd:
            return _BLOG_OK
        if "/api/v1/blog/posts" in cmd:
            return _BLOG_EMPTY
        return "[stub:execute] ok"

    sub_stubs = {
        "execute": execute,
        "write_file": write_file,
        "read_file": read_file,
        "list_files": lambda **kw: json.dumps([]),
    }
    task = _make_task_stub(state, model, "test writer — write pytest test files", sub_stubs)
    wait_tasks = _wait_tasks_stub(state)

    return {
        "execute": execute,
        "write_file": write_file,
        "read_file": read_file,
        "list_files": lambda **kw: json.dumps([]),
        "task": task,
        "wait_tasks": wait_tasks,
        "list_active_tasks": lambda **kw: json.dumps({"tasks": []}),
        "check_task": lambda **kw: json.dumps({"status": "completed"}),
    }


# ── Runner ─────────────────────────────────────────────────────────────────────

async def run_phase(
    spec, stubs: dict, trigger: str, model: str, timeout: int = PHASE_TIMEOUT
) -> AgentRun:
    agent: Agent[Any, str] = Agent(model=model, instructions=spec.system_prompt)
    for name, fn in stubs.items():
        agent.tool_plain(fn, name=name)

    try:
        result = await asyncio.wait_for(
            agent.run(trigger, usage_limits=UsageLimits(request_limit=200)),
            timeout=timeout,
        )
        trace = _trace_from_messages(result.all_messages())
        return AgentRun(response=str(result.output), tool_calls=trace)
    except asyncio.TimeoutError:
        return AgentRun(response="", tool_calls=[], error=f"TIMEOUT after {timeout}s")
    except Exception as e:
        return AgentRun(response="", tool_calls=[], error=f"{type(e).__name__}: {e}")


def _list_dir(path: str) -> list[str]:
    files = []
    if not os.path.isdir(path):
        return files
    for root, dirs, fnames in os.walk(path):
        for fn in sorted(fnames):
            files.append(os.path.relpath(os.path.join(root, fn), path))
    return files


async def main():
    model = _build_real_llm_model()
    print(f"Model: {model}\n{'='*60}", flush=True)

    # Clean slate
    shutil.rmtree("/tmp/proj", ignore_errors=True)
    shutil.rmtree("/tmp/tests", ignore_errors=True)
    state = PipelineState()

    # ── Phase 1: RedditScoutAgent ──────────────────────────────────────────
    print("\n[PHASE 1] RedditScoutAgent", flush=True)
    t0 = time.monotonic()
    rs_stubs = make_reddit_scout_stubs(state, model)
    rs_run = await run_phase(REDDIT_SCOUT, rs_stubs, "kick off your scheduled task", model)
    rs_elapsed = time.monotonic() - t0

    proj_files = _list_dir("/tmp/proj")
    print(f"  elapsed={rs_elapsed:.0f}s  tool_calls={len(rs_run.tool_calls)}  error={rs_run.error}", flush=True)
    print(f"  project_created: {state.project_created is not None}", flush=True)
    print(f"  /tmp/proj/ files: {proj_files}", flush=True)
    print(f"  files pushed to repo: {state.files_pushed}", flush=True)
    print(f"  DMs sent: { {k: len(v) for k, v in state.dm_inbox.items()} }", flush=True)

    # Print main.py snippet if exists
    main_py = "/tmp/proj/main.py"
    if os.path.exists(main_py):
        lines = open(main_py).readlines()
        print(f"\n  main.py ({len(lines)} lines, first 10):")
        for ln in lines[:10]:
            print(f"    {ln}", end="")

    # ── Phase 2: QAAgent ──────────────────────────────────────────────────
    qa_dms = state.inbox_for("qaagent")
    print(f"\n\n[PHASE 2] QAAgent (inbox: {len(qa_dms)} DM(s))", flush=True)
    if qa_dms:
        print(f"  DM content: {qa_dms[0]['content'][:120]}", flush=True)

    t1 = time.monotonic()
    qa_stubs = make_qa_agent_stubs(state, model, qa_dms)
    qa_run = await run_phase(QA_AGENT, qa_stubs, "kick off your scheduled task", model)
    qa_elapsed = time.monotonic() - t1

    test_files = _list_dir("/tmp/tests")
    print(f"  elapsed={qa_elapsed:.0f}s  tool_calls={len(qa_run.tool_calls)}  error={qa_run.error}", flush=True)
    print(f"  /tmp/tests/ files: {test_files}", flush=True)
    print(f"  tests pushed: {state.tests_pushed}", flush=True)

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("PIPELINE RESULTS")
    print(f"{'='*60}")

    checks = {
        "RS: no error":         rs_run.error is None,
        "RS: project created":  state.project_created is not None,
        "RS: code written":     bool(proj_files),
        "RS: main.py present":  "main.py" in proj_files,
        "RS: code pushed":      bool(state.files_pushed),
        "RS: DM → QA":          len(state.inbox_for("qaagent")) > 0,
        "RS: DM → Content":     len(state.inbox_for("contentagent")) > 0,
        "QA: no error":         qa_run.error is None,
        "QA: tests written":    bool(test_files),
        "QA: test_*.py present": any("test_" in f for f in test_files),
        "QA: tests pushed":     bool(state.tests_pushed),
    }

    passed = sum(v for v in checks.values())
    total = len(checks)

    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'}  {name}")

    overall = "PASS" if passed == total else f"PARTIAL ({passed}/{total})"
    print(f"\nResult: {overall}  |  Total time: {rs_elapsed + qa_elapsed:.0f}s")

    if rs_run.error:
        print(f"\nRS error: {rs_run.error}")
    if qa_run.error:
        print(f"QA error: {qa_run.error}")


asyncio.run(main())
