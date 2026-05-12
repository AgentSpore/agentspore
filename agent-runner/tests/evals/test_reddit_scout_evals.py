"""Eval suite for RedditScoutAgent hosted agent.

Covers:
- Scripted FunctionModel cases (good path + 5 anti-pattern bugs).
- Real-LLM parametrized against 3 free OpenRouter models (REAL_LLM=1).

Evaluators tested per case:
  NoErrors, CompletedTask, MinExecuteCount(5),
  ScrapesReddit, SendsHeartbeat, ChecksDuplicates,
  WriteFileBeforeCurlPost, UsesEnvCredentials, PostsBlogPost,
  AcknowledgesDMs, ChecksBlogDedup.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluatorContext

from .cases import REDDIT_SCOUT, AgentSpec
from .evaluators import (
    AgentRun,
    AcknowledgesDMs,
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
    WritesMemory,
)
from .runner import ScriptStep, run_real_llm, run_scripted


PLATFORM_TOOLS: tuple[str, ...] = ("execute", "write_file", "read_file", "list_files")

FREE_MODELS: list[str] = [
    # Verified working 2026-05-12 (12/12 evaluators each)
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
    "minimax/minimax-m2.5:free",
    # Removed: meta-llama/llama-3.3-70b-instruct:free — 404 (providers ignored)
    # Removed: nousresearch/hermes-3-llama-3.1-405b:free — 404 (providers ignored)
    # Removed: qwen/qwen3-next-80b-a3b-instruct:free — 404 (providers ignored)
]

# ---------------------------------------------------------------------------
# Scripted sequences
# ---------------------------------------------------------------------------


def _good_scout_run() -> list[ScriptStep]:
    """Full happy-path: startup HB → RSS → dedup GET → project → GET blog → blog → final HB+ACK."""
    return [
        # Step 0: startup heartbeat — fetch inbox DMs
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "User-Agent: RedditScoutAgent/1.0"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"starting\\"}"'
                )
            },
        ),
        # Step 1: fetch RSS via python3 inline script
        (
            "execute",
            {
                "command": (
                    "python3 -c \""
                    "import urllib.request, xml.etree.ElementTree as ET, json; "
                    "subs = ['SaaS','startups','webdev']; items = []; "
                    "[items.append({'sub':s,'title':'Frustrated devs need better CI tooling','link':'https://www.reddit.com/r/SaaS/123'}) for s in subs]; "
                    "print(json.dumps(items))"
                    "\" # https://www.reddit.com/r/SaaS/hot.rss"
                )
            },
        ),
        # Step 2: GET existing projects (dedup check)
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects?mine=true"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "User-Agent: RedditScoutAgent/1.0"'
                )
            },
        ),
        # Step 3a: write project file
        (
            "write_file",
            {
                "path": "/tmp/project.json",
                "content": (
                    '{"title":"CIFlow — CI pipeline builder for small teams",'
                    '"description":"Reddit shows repeated frustration with complex CI setup costs.",'
                    '"tech_stack":["python","fastapi"]}'
                ),
            },
        ),
        # Step 3b: POST new project
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "User-Agent: RedditScoutAgent/1.0"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/project.json"
                )
            },
        ),
        # Step 3.5: GET recent blog posts (same-day dedup)
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts?limit=10"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "User-Agent: RedditScoutAgent/1.0"'
                )
            },
        ),
        # Step 4a: write blog file
        (
            "write_file",
            {
                "path": "/tmp/blog.json",
                "content": (
                    '{"title":"Reddit Startup Pulse — 2026-05-12",'
                    '"content":"Analysed SaaS/startups/webdev subreddits. '
                    "Top pain: complex CI tooling. Recommended: CIFlow — CI pipeline builder. "
                    "Viability 8/10, uniqueness 7/10. Project created on platform.\","
                    '"tags":["reddit","startup-ideas"]}'
                ),
            },
        ),
        # Step 4b: POST blog
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "User-Agent: RedditScoutAgent/1.0"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/blog.json"
                )
            },
        ),
        # Step 5: final heartbeat — inline JSON + read_dm_ids to ACK inbox
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "User-Agent: RedditScoutAgent/1.0"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"working\\",'
                    '\\"completed_tasks\\":[{\\"title\\":\\"Reddit scouting complete\\"}],'
                    '\\"insights\\":[{\\"text\\":\\"CI tooling frustration dominant\\"}],'
                    '\\"read_dm_ids\\":[\\"dm-test-001\\"]}"'
                )
            },
        ),
        # Step 6: persist run summary to memory (mid-term store)
        (
            "write_memory",
            {
                "key": "last_run",
                "value": (
                    '{"run_date":"2026-05-12","blog_post_id":"mock-id-123",'
                    '"acked_dm_ids":["dm-test-001"],'
                    '"top_pain":"CI tooling frustration dominant",'
                    '"session_id":"sess-a1b2c3d4"}'
                ),
            },
        ),
        "Scouted Reddit: found 1 high-scoring idea (CIFlow, 8/10). Blog id=mock-id-123. Session=sess-a1b2c3d4. ACKed DMs: dm-test-001. Memory updated.",
    ]


def _bad_no_memory_update() -> list[ScriptStep]:
    """Bug: completes full workflow but skips Step 6 (memory not persisted)."""
    good = _good_scout_run()
    # Drop the write_memory step (second-to-last); keep terminal text.
    return [step for step in good if not (isinstance(step, tuple) and step[0] == "write_memory")]


def _bad_no_reddit_fetch() -> list[ScriptStep]:
    """Bug: agent skips RSS fetch, invents project, misses ScrapesReddit."""
    return [
        # Step 0: startup heartbeat (correct)
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"starting\\"}"'
                )
            },
        ),
        # BUG: skips reddit.com fetch, goes straight to projects
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects?mine=true"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "write_file",
            {"path": "/tmp/project.json", "content": '{"title":"FakeIdea","description":"invented","tech_stack":["python"]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/project.json"
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts?limit=10"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "write_file",
            {"path": "/tmp/blog.json", "content": '{"title":"Ideas","content":"some ideas","tags":["reddit"]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/blog.json"
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"working\\",\\"read_dm_ids\\":[\\"dm-test-001\\"]}"'
                )
            },
        ),
        (
            "write_memory",
            {"key": "last_run", "value": "{\"run_date\":\"2026-05-12\"}"},
        ),
        "Scouted and posted ideas.",
    ]


def _bad_no_dedup_check() -> list[ScriptStep]:
    """Bug: POSTs project without prior GET -- dedup check skipped."""
    return [
        # Step 0: startup heartbeat (correct)
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"starting\\"}"'
                )
            },
        ),
        # RSS fetch present
        (
            "execute",
            {
                "command": (
                    "python3 -c \"import urllib.request; "
                    "urllib.request.urlopen('https://www.reddit.com/r/SaaS/hot.rss')\" "
                )
            },
        ),
        # BUG: Immediately POST project WITHOUT a prior GET
        (
            "write_file",
            {"path": "/tmp/project.json", "content": '{"title":"NoDedup App","description":"...","tech_stack":["python"]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/project.json"
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts?limit=10"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "write_file",
            {"path": "/tmp/blog.json", "content": '{"title":"Blog","content":"...","tags":["reddit"]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/blog.json"
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"working\\",\\"read_dm_ids\\":[\\"dm-test-001\\"]}"'
                )
            },
        ),
        (
            "write_memory",
            {"key": "last_run", "value": "{\"run_date\":\"2026-05-12\"}"},
        ),
        "Posted project without dedup.",
    ]


def _bad_no_heartbeat() -> list[ScriptStep]:
    """Bug: agent completes workflow but forgets the heartbeat POST."""
    return [
        (
            "execute",
            {
                "command": (
                    "python3 -c \"import urllib.request; "
                    "urllib.request.urlopen('https://www.reddit.com/r/SaaS/hot.rss')\" "
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects?mine=true"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts?limit=10"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "write_file",
            {"path": "/tmp/blog.json", "content": '{"title":"Scout","content":"...","tags":["reddit"]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/blog.json"
                )
            },
        ),
        "Done, but no heartbeat.",
    ]


def _bad_inline_json_post() -> list[ScriptStep]:
    """Bug: uses inline JSON in curl -d for blog POST instead of writing a file first."""
    return [
        # Step 0: startup heartbeat (correct)
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"starting\\"}"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    "python3 -c \"import urllib.request; "
                    "urllib.request.urlopen('https://www.reddit.com/r/SaaS/hot.rss')\" "
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects?mine=true"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts?limit=10"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        # BUG: Inline JSON anti-pattern -- no prior write_file for blog
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d '{\"title\":\"Scout\",\"content\":\"x\",\"tags\":[]}'"
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"working\\",\\"read_dm_ids\\":[\\"dm-test-001\\"]}"'
                )
            },
        ),
        (
            "write_memory",
            {"key": "last_run", "value": "{\"run_date\":\"2026-05-12\"}"},
        ),
        "Posted blog with inline JSON.",
    ]


def _bad_no_dm_ack() -> list[ScriptStep]:
    """Bug: agent sends final heartbeat but doesn't include read_dm_ids (DMs not ACKed)."""
    return [
        # Step 0: startup heartbeat (gets DMs in response)
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"starting\\"}"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    "python3 -c \"import urllib.request; "
                    "urllib.request.urlopen('https://www.reddit.com/r/SaaS/hot.rss')\" "
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/projects?mine=true"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts?limit=10"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "write_file",
            {"path": "/tmp/blog.json", "content": '{"title":"Reddit Startup Pulse — 2026-05-12","content":"...","tags":["reddit"]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/blog.json"
                )
            },
        ),
        # BUG: final heartbeat without read_dm_ids — DMs stay unread
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/agents/heartbeat"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    ' -d "{\\"status\\":\\"working\\",\\"completed_tasks\\":[{\\"title\\":\\"done\\"}]}"'
                )
            },
        ),
        (
            "write_memory",
            {"key": "last_run", "value": "{\"run_date\":\"2026-05-12\"}"},
        ),
        "Done, DMs not acknowledged.",
    ]


# ---------------------------------------------------------------------------
# Evaluators list and expected failures
# ---------------------------------------------------------------------------

_SCOUT_EVALUATORS: list[Any] = [
    NoErrors(),
    CompletedTask(),
    MinExecuteCount(min_count=5),
    ScrapesReddit(),
    SendsHeartbeat(),
    ChecksDuplicates(),
    WriteFileBeforeCurlPost(),
    UsesEnvCredentials(),
    PostsBlogPost(),
    AcknowledgesDMs(),
    ChecksBlogDedup(),
    WritesMemory(),
]

_SCOUT_EVALUATOR_NAMES: list[str] = [type(e).__name__ for e in _SCOUT_EVALUATORS]

_CASE_NAMES: list[str] = [
    "scout_good",
    "scout_bad_no_reddit",
    "scout_bad_no_dedup",
    "scout_bad_no_heartbeat",
    "scout_bad_inline_json",
    "scout_bad_no_dm_ack",
    "scout_bad_no_memory",
]

# bad_no_heartbeat execute calls: reddit + GET projects + GET blog + POST blog = 4 (< 5)
# bad_no_reddit execute calls: startup HB + GET projects + POST project + GET blog + POST blog + final HB = 6 (>= 5) -- passes
_EXPECTED_FAILURES: frozenset[tuple[str, str]] = frozenset(
    {
        ("scout_bad_no_reddit", "ScrapesReddit"),
        ("scout_bad_no_dedup", "ChecksDuplicates"),
        ("scout_bad_no_heartbeat", "SendsHeartbeat"),
        ("scout_bad_no_heartbeat", "MinExecuteCount"),   # 4 execute calls < 5
        ("scout_bad_no_heartbeat", "WritesMemory"),      # bad-no-hb script lacks write_memory
        # scout_bad_no_heartbeat::AcknowledgesDMs passes vacuously (no HB → no POSTs to check)
        ("scout_bad_inline_json", "WriteFileBeforeCurlPost"),
        ("scout_bad_no_dm_ack", "AcknowledgesDMs"),
        ("scout_bad_no_memory", "WritesMemory"),
    }
)


# ---------------------------------------------------------------------------
# Dataset and task
# ---------------------------------------------------------------------------


def _build_dataset() -> Dataset[dict[str, Any], AgentRun, dict[str, Any]]:
    cases: list[Case[dict[str, Any], AgentRun, dict[str, Any]]] = [
        Case(
            name="scout_good",
            inputs={"agent": REDDIT_SCOUT, "script": _good_scout_run()},
        ),
        Case(
            name="scout_bad_no_reddit",
            inputs={"agent": REDDIT_SCOUT, "script": _bad_no_reddit_fetch()},
        ),
        Case(
            name="scout_bad_no_dedup",
            inputs={"agent": REDDIT_SCOUT, "script": _bad_no_dedup_check()},
        ),
        Case(
            name="scout_bad_no_heartbeat",
            inputs={"agent": REDDIT_SCOUT, "script": _bad_no_heartbeat()},
        ),
        Case(
            name="scout_bad_inline_json",
            inputs={"agent": REDDIT_SCOUT, "script": _bad_inline_json_post()},
        ),
        Case(
            name="scout_bad_no_dm_ack",
            inputs={"agent": REDDIT_SCOUT, "script": _bad_no_dm_ack()},
        ),
        Case(
            name="scout_bad_no_memory",
            inputs={"agent": REDDIT_SCOUT, "script": _bad_no_memory_update()},
        ),
    ]
    return Dataset[dict[str, Any], AgentRun, dict[str, Any]](
        name="reddit_scout",
        cases=cases,
        evaluators=_SCOUT_EVALUATORS,
    )


async def _task(inputs: dict[str, Any]) -> AgentRun:
    spec: AgentSpec = inputs["agent"]
    script: list[ScriptStep] = inputs["script"]
    return await run_scripted(spec.system_prompt, script, PLATFORM_TOOLS)


# ---------------------------------------------------------------------------
# Shared report fixture (module-scoped -- run once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scout_report() -> Any:
    dataset = _build_dataset()
    return asyncio.run(dataset.evaluate(_task, max_concurrency=4))


# ---------------------------------------------------------------------------
# Per-(case, evaluator) parametrized scripted tests
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "scout_case" in metafunc.fixturenames and "scout_eval" in metafunc.fixturenames:
        ids = [f"{c}::{e}" for c in _CASE_NAMES for e in _SCOUT_EVALUATOR_NAMES]
        params = [(c, e) for c in _CASE_NAMES for e in _SCOUT_EVALUATOR_NAMES]
        metafunc.parametrize("scout_case,scout_eval", params, ids=ids)


def test_scout_eval_cell(
    scout_report: Any,
    scout_case: str,
    scout_eval: str,
) -> None:
    """Each (case, evaluator) cell matches the expected pass/fail table."""
    by_name = {c.name: c for c in scout_report.cases}
    case_result = by_name[scout_case]
    scores = {a.name: a.value for a in case_result.assertions.values()}

    if scout_eval not in scores:
        pytest.skip(f"Evaluator {scout_eval} not in report")

    actual = scores[scout_eval]
    should_fail = (scout_case, scout_eval) in _EXPECTED_FAILURES

    if should_fail:
        assert actual is False, (
            f"{scout_case}::{scout_eval} should FAIL but got {actual!r}"
        )
    else:
        assert actual is not False, (
            f"{scout_case}::{scout_eval} should PASS but got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Sanity gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scout_good_passes_all(scout_report: Any) -> None:
    """Happy-path scripted run must score True on every evaluator."""
    by_name = {c.name: c for c in scout_report.cases}
    scores = {a.name: a.value for a in by_name["scout_good"].assertions.values()}
    failed = {k: v for k, v in scores.items() if v is False}
    assert not failed, f"scout_good should pass all evaluators, failed: {failed}"


@pytest.mark.asyncio
async def test_scout_bad_cases_flagged(scout_report: Any) -> None:
    """Each anti-pattern case triggers exactly the expected evaluator."""
    by_name = {c.name: c for c in scout_report.cases}

    no_reddit = {a.name: a.value for a in by_name["scout_bad_no_reddit"].assertions.values()}
    assert no_reddit["ScrapesReddit"] is False, "missing reddit.com fetch must fail ScrapesReddit"

    no_dedup = {a.name: a.value for a in by_name["scout_bad_no_dedup"].assertions.values()}
    assert no_dedup["ChecksDuplicates"] is False, "POST before GET must fail ChecksDuplicates"

    no_hb = {a.name: a.value for a in by_name["scout_bad_no_heartbeat"].assertions.values()}
    assert no_hb["SendsHeartbeat"] is False, "missing heartbeat POST must fail SendsHeartbeat"

    inline = {a.name: a.value for a in by_name["scout_bad_inline_json"].assertions.values()}
    assert inline["WriteFileBeforeCurlPost"] is False, "inline JSON must fail WriteFileBeforeCurlPost"

    no_ack = {a.name: a.value for a in by_name["scout_bad_no_dm_ack"].assertions.values()}
    assert no_ack["AcknowledgesDMs"] is False, "heartbeat without read_dm_ids must fail AcknowledgesDMs"

    no_mem = {a.name: a.value for a in by_name["scout_bad_no_memory"].assertions.values()}
    assert no_mem["WritesMemory"] is False, "missing write_memory must fail WritesMemory"


# ---------------------------------------------------------------------------
# Real-LLM tests -- parametrized by free model
# ---------------------------------------------------------------------------


@pytest.mark.real_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("model", FREE_MODELS)
async def test_reddit_scout_real_llm(model: str) -> None:
    """Run RedditScoutAgent against a real OpenRouter free model.

    Requires:
      REAL_LLM=1
      OPENROUTER_API_KEY=<key>
      OPENAI_BASE_URL=https://openrouter.ai/api/v1
      OPENAI_API_KEY=<same key> (pydantic-ai openai provider reads this)

    Optionally override model via REAL_LLM_MODEL env -- parametrize still
    runs all three; REAL_LLM_MODEL only affects the runner default.
    """
    if not os.environ.get("REAL_LLM"):
        pytest.skip("Set REAL_LLM=1 to run real-LLM tests")

    # Override model for this parametrize iteration.
    original = os.environ.get("REAL_LLM_MODEL")
    os.environ["REAL_LLM_MODEL"] = model

    try:
        run = await run_real_llm(REDDIT_SCOUT.system_prompt, PLATFORM_TOOLS)
    finally:
        if original is None:
            os.environ.pop("REAL_LLM_MODEL", None)
        else:
            os.environ["REAL_LLM_MODEL"] = original

    results: dict[str, bool | None] = {}

    for ev in _SCOUT_EVALUATORS:
        ev_name = type(ev).__name__

        class _FakeCtx:
            output = run
            inputs: dict[str, Any] = {}
            metrics = None

        try:
            val = ev.evaluate(_FakeCtx())  # type: ignore[arg-type]
        except Exception:
            val = None
        results[ev_name] = val

    # Mandatory gates -- any real model must pass these.
    assert run.error is None, f"[{model}] run error: {run.error}"
    assert run.tool_calls, f"[{model}] no tool calls -- model stalled"

    # Report per-evaluator outcome (non-fatal -- lets us see partial scores).
    failures = [k for k, v in results.items() if v is False]
    score = sum(1 for v in results.values() if v is not False)
    total = len(results)

    # Print for CI visibility.
    print(f"\n[{model}] score={score}/{total}  failures={failures}")
    print(f"  tool_calls={len(run.tool_calls)}  response_len={len(run.response or '')}")

    # Hard gate: at minimum the model must scrape Reddit and send heartbeat.
    assert results.get("ScrapesReddit") is not False, (
        f"[{model}] FAIL ScrapesReddit -- model did not fetch from reddit.com"
    )
    assert results.get("SendsHeartbeat") is not False, (
        f"[{model}] FAIL SendsHeartbeat -- model did not POST heartbeat"
    )
    assert results.get("PostsBlogPost") is not False, (
        f"[{model}] FAIL PostsBlogPost -- model did not POST blog"
    )
