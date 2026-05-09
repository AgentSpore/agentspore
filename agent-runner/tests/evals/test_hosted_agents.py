"""End-to-end eval suite for hosted platform agents.

Each Case ships a scripted tool-call sequence (good vs anti-pattern) so we
can verify the evaluators catch the right failure modes. When real-LLM evals
are wired up, swap runner.run_scripted for runner.run_real_llm -- evaluators
stay identical.

Test layout
-----------
test_eval_case_evaluator  -- parametrized per (case_name, evaluator_name)
test_good_cases_pass_all  -- sanity gate: good runs fail nothing
test_bad_cases_flagged    -- explicit assertions on which evaluator fires

The per-(case, evaluator) parametrize means pytest output shows the exact
dimension that failed, not one giant assertion.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationResult

from .cases import CONTENT_AGENT, PLATFORM_ANALYST, QA_AGENT, AgentSpec
from .evaluators import (
    AgentRun,
    CompletedTask,
    CostUnder,
    HitsExpectedEndpoint,
    MinExecuteCount,
    NoErrors,
    NoHallucinatedNumbers,
    OnlyKnownEndpoints,
    PostsBlogPost,
    UsesEnvCredentials,
    WriteFileBeforeCurlPost,
)
from .runner import ScriptStep, run_real_llm, run_scripted


PLATFORM_TOOLS: tuple[str, ...] = ("execute", "write_file", "read_file", "list_files")

# ---------------------------------------------------------------------------
# Scripted sequences (good paths)
# ---------------------------------------------------------------------------


def _good_content_run() -> list[ScriptStep]:
    return [
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/agents?limit=20"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "write_file",
            {
                "path": "/tmp/post.json",
                "content": '{"title":"Community agents round-up","content":"Active agents: 42. AgentBot leads with 18 tasks.","tags":["community","update"]}',
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/post.json"
                )
            },
        ),
        "Published blog post about top community agents. Active agents: 42.",
    ]


def _good_pulse_run() -> list[ScriptStep]:
    return [
        (
            "execute",
            {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/stats"'},
        ),
        (
            "write_file",
            {
                "path": "/tmp/pulse.json",
                "content": '{"title":"Platform Pulse 2026-05-09","content":"Total agents: 128. Active today: 37.","tags":["analytics","pulse"]}',
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/pulse.json"
                )
            },
        ),
        "Published Platform Pulse. Reported 128 total agents, 37 active today.",
    ]


def _good_qa_run() -> list[ScriptStep]:
    return [
        (
            "execute",
            {
                "command": (
                    "curl -s -o /dev/null -w '%{http_code}'"
                    ' "$AGENTSPORE_PLATFORM_URL/api/v1/agents/stats"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    "curl -s -o /dev/null -w '%{http_code}'"
                    ' "$AGENTSPORE_PLATFORM_URL/health"'
                )
            },
        ),
        (
            "write_file",
            {
                "path": "/tmp/qa.json",
                "content": '{"title":"Health Check 2026-05-09 12:00","content":"all 200","tags":["health","qa"]}',
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d @/tmp/qa.json"
                )
            },
        ),
        "Health check posted.",
    ]


# ---------------------------------------------------------------------------
# Scripted sequences (anti-patterns / bug reproductions)
# ---------------------------------------------------------------------------


def _bad_content_run_inline_json() -> list[ScriptStep]:
    """Bug 1: inline JSON in curl -d breaks on nested quotes."""
    return [
        (
            "execute",
            {
                "command": (
                    'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/agents?limit=20"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                )
            },
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    ' -H "Content-Type: application/json"'
                    " -d '{\"title\":\"X\",\"content\":\"Y\",\"tags\":[]}'"
                )
            },
        ),
        "Published.",
    ]


def _bad_content_run_done_only() -> list[ScriptStep]:
    """Bug 2: agent stops mid-workflow after fetch, never publishes."""
    return [
        (
            "execute",
            {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/agents?limit=20"'},
        ),
        "Done.",
    ]


def _bad_content_hardcoded_key() -> list[ScriptStep]:
    """Bug 3: hardcoded API key and host instead of env vars."""
    return [
        (
            "execute",
            {
                "command": (
                    'curl -s "https://agentspore.com/api/v1/public/agents"'
                    ' -H "X-API-Key: af_hardcoded123"'
                )
            },
        ),
        (
            "write_file",
            {"path": "/tmp/post.json", "content": '{"title":"X","content":"Y","tags":[]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -X POST "https://agentspore.com/api/v1/blog/posts"'
                    ' -H "X-API-Key: af_hardcoded123"'
                    " -d @/tmp/post.json"
                )
            },
        ),
        "Posted.",
    ]


def _bad_wrong_endpoint() -> list[ScriptStep]:
    """Bug 4: agent hits /api/v1/public/stats (404) instead of /api/v1/agents/stats."""
    return [
        (
            "execute",
            {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/stats"'},
        ),
        (
            "write_file",
            {"path": "/tmp/pulse.json", "content": '{"title":"Pulse","content":"...","tags":[]}'},
        ),
        (
            "execute",
            {
                "command": (
                    'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts"'
                    ' -H "X-API-Key: $AGENTSPORE_API_KEY"'
                    " -d @/tmp/pulse.json"
                )
            },
        ),
        "Published.",
    ]


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

_ALL_EVALUATORS: list[Any] = [
    NoErrors(),
    CompletedTask(),
    MinExecuteCount(min_count=2),
    WriteFileBeforeCurlPost(),
    UsesEnvCredentials(),
    HitsExpectedEndpoint(),
    OnlyKnownEndpoints(),
    PostsBlogPost(),
    CostUnder(max_usd=0.01),
    NoHallucinatedNumbers(),
]


def _build_dataset() -> Dataset[dict[str, Any], AgentRun, dict[str, Any]]:
    cases: list[Case[dict[str, Any], AgentRun, dict[str, Any]]] = [
        Case(
            name="content_good",
            inputs={
                "agent": CONTENT_AGENT,
                "script": _good_content_run(),
                "expected_endpoint": "/api/v1/blog/posts",
                "expected_numbers": [42, 18],
            },
        ),
        Case(
            name="content_bad_inline_json",
            inputs={
                "agent": CONTENT_AGENT,
                "script": _bad_content_run_inline_json(),
                "expected_endpoint": "/api/v1/blog/posts",
            },
        ),
        Case(
            name="content_bad_done_only",
            inputs={
                "agent": CONTENT_AGENT,
                "script": _bad_content_run_done_only(),
                "expected_endpoint": "/api/v1/blog/posts",
            },
        ),
        Case(
            name="content_bad_hardcoded_key",
            inputs={
                "agent": CONTENT_AGENT,
                "script": _bad_content_hardcoded_key(),
                "expected_endpoint": "/api/v1/blog/posts",
            },
        ),
        Case(
            name="content_bad_wrong_endpoint",
            inputs={
                "agent": PLATFORM_ANALYST,
                "script": _bad_wrong_endpoint(),
                "expected_endpoint": "/api/v1/agents/stats",
            },
        ),
        Case(
            name="analyst_good",
            inputs={
                "agent": PLATFORM_ANALYST,
                "script": _good_pulse_run(),
                "expected_endpoint": "/api/v1/agents/stats",
                "expected_numbers": [128, 37],
            },
        ),
        Case(
            name="qa_good",
            inputs={
                "agent": QA_AGENT,
                "script": _good_qa_run(),
                "expected_endpoint": "/health",
            },
        ),
    ]
    return Dataset[dict[str, Any], AgentRun, dict[str, Any]](
        name="hosted_agents",
        cases=cases,
        evaluators=_ALL_EVALUATORS,
    )


# ---------------------------------------------------------------------------
# Task function
# ---------------------------------------------------------------------------


async def _task(inputs: dict[str, Any]) -> AgentRun:
    spec: AgentSpec = inputs["agent"]
    script: list[ScriptStep] = inputs["script"]
    return await run_scripted(spec.system_prompt, script, PLATFORM_TOOLS)


# ---------------------------------------------------------------------------
# Shared report fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def eval_report() -> Any:
    """Run the full dataset once per module; share across all parametrized tests."""
    dataset = _build_dataset()
    return asyncio.run(dataset.evaluate(_task, max_concurrency=4))


# ---------------------------------------------------------------------------
# Per-(case, evaluator) parametrized test
# ---------------------------------------------------------------------------

_CASE_NAMES: list[str] = [
    "content_good",
    "content_bad_inline_json",
    "content_bad_done_only",
    "content_bad_hardcoded_key",
    "content_bad_wrong_endpoint",
    "analyst_good",
    "qa_good",
]

_EVALUATOR_NAMES: list[str] = [type(e).__name__ for e in _ALL_EVALUATORS]

# Expected failures: set of (case_name, evaluator_name) that SHOULD fail.
# content_bad_inline_json has 2 execute calls (fetch + POST) so MinExecuteCount passes.
# PostsBlogPost passes because the curl does hit /api/v1/blog/posts (just with bad -d syntax).
_EXPECTED_FAILURES: frozenset[tuple[str, str]] = frozenset(
    {
        ("content_bad_inline_json", "WriteFileBeforeCurlPost"),
        ("content_bad_done_only", "CompletedTask"),
        ("content_bad_done_only", "MinExecuteCount"),
        ("content_bad_done_only", "HitsExpectedEndpoint"),
        ("content_bad_done_only", "PostsBlogPost"),
        ("content_bad_hardcoded_key", "UsesEnvCredentials"),
        ("content_bad_wrong_endpoint", "OnlyKnownEndpoints"),
        ("content_bad_wrong_endpoint", "HitsExpectedEndpoint"),
    }
)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize test_eval_cell with all (case, evaluator) combinations."""
    if "case_name" in metafunc.fixturenames and "evaluator_name" in metafunc.fixturenames:
        ids = [f"{c}::{e}" for c in _CASE_NAMES for e in _EVALUATOR_NAMES]
        params = [(c, e) for c in _CASE_NAMES for e in _EVALUATOR_NAMES]
        metafunc.parametrize("case_name,evaluator_name", params, ids=ids)


def test_eval_cell(
    eval_report: Any,
    case_name: str,
    evaluator_name: str,
) -> None:
    """Each (case, evaluator) cell matches the expected pass/fail table."""
    by_name = {c.name: c for c in eval_report.cases}
    case_result = by_name[case_name]
    scores = {a.name: a.value for a in case_result.assertions.values()}

    if evaluator_name not in scores:
        pytest.skip(f"Evaluator {evaluator_name} not in report")

    actual = scores[evaluator_name]
    should_fail = (case_name, evaluator_name) in _EXPECTED_FAILURES

    if should_fail:
        assert actual is False, (
            f"{case_name}::{evaluator_name} should FAIL but got {actual!r}"
        )
    else:
        assert actual is not False, (
            f"{case_name}::{evaluator_name} should PASS but got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Integration sanity gates (kept for CI readability)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_good_cases_pass_all(eval_report: Any) -> None:
    """Good scripted runs must score True on every evaluator."""
    by_name = {c.name: c for c in eval_report.cases}
    for good in ("content_good", "analyst_good", "qa_good"):
        scores = {a.name: a.value for a in by_name[good].assertions.values()}
        failed = {k: v for k, v in scores.items() if v is False}
        assert not failed, f"{good} should pass all evaluators, failed: {failed}"


@pytest.mark.asyncio
async def test_bad_cases_flagged(eval_report: Any) -> None:
    """Specific anti-pattern cases must trigger the correct evaluator."""
    by_name = {c.name: c for c in eval_report.cases}

    inline = {a.name: a.value for a in by_name["content_bad_inline_json"].assertions.values()}
    assert inline["WriteFileBeforeCurlPost"] is False, "inline JSON must fail WriteFileBeforeCurlPost"

    done_only = {a.name: a.value for a in by_name["content_bad_done_only"].assertions.values()}
    assert done_only["CompletedTask"] is False, "'Done.' response must fail CompletedTask"
    assert done_only["MinExecuteCount"] is False, "single execute must fail MinExecuteCount"
    assert done_only["HitsExpectedEndpoint"] is False, "missing /blog/posts call must fail HitsExpectedEndpoint"

    hardcoded = {a.name: a.value for a in by_name["content_bad_hardcoded_key"].assertions.values()}
    assert hardcoded["UsesEnvCredentials"] is False, "hardcoded API key must fail UsesEnvCredentials"

    wrong_ep = {a.name: a.value for a in by_name["content_bad_wrong_endpoint"].assertions.values()}
    assert wrong_ep["OnlyKnownEndpoints"] is False, "unknown /public/stats must fail OnlyKnownEndpoints"
    assert wrong_ep["HitsExpectedEndpoint"] is False, "wrong endpoint must fail HitsExpectedEndpoint"


# ---------------------------------------------------------------------------
# Real-LLM tests (skipped by default)
# ---------------------------------------------------------------------------


@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_content_agent_real_llm() -> None:
    """Smoke test ContentAgent with a real OpenRouter model.

    Requires: REAL_LLM=1, OPENROUTER_API_KEY, OPENAI_BASE_URL=https://openrouter.ai/api/v1
    """
    if not os.environ.get("REAL_LLM"):
        pytest.skip("Set REAL_LLM=1 to run real-LLM tests")

    run = await run_real_llm(CONTENT_AGENT.system_prompt, PLATFORM_TOOLS)
    assert run.error is None, f"Real-LLM run failed: {run.error}"
    assert run.tool_calls, "No tool calls from real LLM -- workflow stalled"


# ---------------------------------------------------------------------------
# Report helper
# ---------------------------------------------------------------------------


def make_markdown_report(report: Any) -> str:
    """Render a markdown table from an EvaluationReport.

    The platform cron can pass this output to /api/v1/blog/posts to close the
    loop between eval quality metrics and public visibility.
    """
    evaluator_names = _EVALUATOR_NAMES
    header = "| Case | " + " | ".join(evaluator_names) + " | Overall |"
    separator = "|---" + "|---" * (len(evaluator_names) + 1) + "|"
    rows: list[str] = [header, separator]

    total_pass = 0
    total_cells = 0

    for case_result in report.cases:
        scores = {a.name: a.value for a in case_result.assertions.values()}
        cells: list[str] = []
        case_pass = 0
        for ev in evaluator_names:
            val = scores.get(ev)
            if val is True:
                cells.append("OK")
                case_pass += 1
            elif val is False:
                cells.append("FAIL")
            else:
                cells.append("-")
                case_pass += 1  # vacuous pass counts as pass
        total_pass += case_pass
        total_cells += len(evaluator_names)
        pct = round(case_pass / len(evaluator_names) * 100)
        rows.append(f"| {case_result.name} | " + " | ".join(cells) + f" | {pct}% |")

    avg = round(total_pass / total_cells * 100) if total_cells else 0
    rows.append(f"\n**Average score: {avg}%**")
    return "\n".join(rows)


if __name__ == "__main__":
    # Convenience: `python -m tests.evals.test_hosted_agents` prints a report.
    dataset = _build_dataset()
    _report = asyncio.run(dataset.evaluate(_task, max_concurrency=4))
    _report.print(include_input=False, include_output=False, include_durations=True)
    print()
    print(make_markdown_report(_report))
