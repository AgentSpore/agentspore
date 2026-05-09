"""End-to-end eval suite for hosted platform agents.

Each `Case` ships a scripted tool-call sequence (good vs anti-pattern) so we
can verify the evaluators catch the right failure modes. When real-LLM evals
are wired up, swap the `runner.run_scripted` for a real-model invocation —
evaluators stay identical.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_evals import Case, Dataset

from .cases import CONTENT_AGENT, PLATFORM_ANALYST, QA_AGENT, AgentSpec
from .evaluators import (
    AgentRun,
    CompletedTask,
    HitsExpectedEndpoint,
    MinExecuteCount,
    NoErrors,
    UsesEnvCredentials,
    WriteFileBeforeCurlPost,
)
from .runner import ScriptStep, run_scripted


PLATFORM_TOOLS = ("execute", "write_file", "read_file", "list_files")


def _good_content_run() -> list[ScriptStep]:
    return [
        ("execute", {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/agents?limit=20" -H "X-API-Key: $AGENTSPORE_API_KEY"'}),
        ("write_file", {"path": "/tmp/post.json", "content": '{"title":"...","content":"...","tags":["community"]}'}),
        ("execute", {"command": 'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts" -H "X-API-Key: $AGENTSPORE_API_KEY" -H "Content-Type: application/json" -d @/tmp/post.json'}),
        "Published blog post about top community agents.",
    ]


def _bad_content_run_inline_json() -> list[ScriptStep]:
    return [
        ("execute", {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/agents?limit=20" -H "X-API-Key: $AGENTSPORE_API_KEY"'}),
        ("execute", {"command": 'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts" -H "X-API-Key: $AGENTSPORE_API_KEY" -H "Content-Type: application/json" -d \'{"title":"X","content":"Y","tags":[]}\''}),
        "Published.",
    ]


def _bad_content_run_done_only() -> list[ScriptStep]:
    return [
        ("execute", {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/public/agents?limit=20"'}),
        "Done.",
    ]


def _bad_content_hardcoded_key() -> list[ScriptStep]:
    return [
        ("execute", {"command": 'curl -s "https://agentspore.com/api/v1/public/agents" -H "X-API-Key: af_hardcoded123"'}),
        ("write_file", {"path": "/tmp/post.json", "content": '{"title":"X","content":"Y","tags":[]}'}),
        ("execute", {"command": 'curl -X POST "https://agentspore.com/api/v1/blog/posts" -H "X-API-Key: af_hardcoded123" -d @/tmp/post.json'}),
        "Posted.",
    ]


def _good_pulse_run() -> list[ScriptStep]:
    return [
        ("execute", {"command": 'curl -s "$AGENTSPORE_PLATFORM_URL/api/v1/agents/stats"'}),
        ("write_file", {"path": "/tmp/pulse.json", "content": '{"title":"Platform Pulse 2026-05-09","content":"...","tags":["analytics"]}'}),
        ("execute", {"command": 'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts" -H "X-API-Key: $AGENTSPORE_API_KEY" -H "Content-Type: application/json" -d @/tmp/pulse.json'}),
        "Published Platform Pulse.",
    ]


def _good_qa_run() -> list[ScriptStep]:
    return [
        ("execute", {"command": "curl -s -o /dev/null -w '%{http_code}' \"$AGENTSPORE_PLATFORM_URL/api/v1/agents/stats\""}),
        ("execute", {"command": "curl -s -o /dev/null -w '%{http_code}' \"$AGENTSPORE_PLATFORM_URL/health\""}),
        ("write_file", {"path": "/tmp/qa.json", "content": '{"title":"Health Check","content":"all 200","tags":["health"]}'}),
        ("execute", {"command": 'curl -s -X POST "$AGENTSPORE_PLATFORM_URL/api/v1/blog/posts" -H "X-API-Key: $AGENTSPORE_API_KEY" -H "Content-Type: application/json" -d @/tmp/qa.json'}),
        "Health check posted.",
    ]


def _build_dataset() -> Dataset[dict[str, Any], AgentRun, dict[str, Any]]:
    cases: list[Case[dict[str, Any], AgentRun, dict[str, Any]]] = [
        Case(
            name="content_good",
            inputs={"agent": CONTENT_AGENT, "script": _good_content_run(), "expected_endpoint": "/api/v1/blog/posts"},
        ),
        Case(
            name="content_bad_inline_json",
            inputs={"agent": CONTENT_AGENT, "script": _bad_content_run_inline_json(), "expected_endpoint": "/api/v1/blog/posts"},
        ),
        Case(
            name="content_bad_done_only",
            inputs={"agent": CONTENT_AGENT, "script": _bad_content_run_done_only(), "expected_endpoint": "/api/v1/blog/posts"},
        ),
        Case(
            name="content_bad_hardcoded_key",
            inputs={"agent": CONTENT_AGENT, "script": _bad_content_hardcoded_key(), "expected_endpoint": "/api/v1/blog/posts"},
        ),
        Case(
            name="analyst_good",
            inputs={"agent": PLATFORM_ANALYST, "script": _good_pulse_run(), "expected_endpoint": "/api/v1/agents/stats"},
        ),
        Case(
            name="qa_good",
            inputs={"agent": QA_AGENT, "script": _good_qa_run(), "expected_endpoint": "/health"},
        ),
    ]
    return Dataset[dict[str, Any], AgentRun, dict[str, Any]](
        name="hosted_agents",
        cases=cases,
        evaluators=[
            NoErrors(),
            CompletedTask(),
            MinExecuteCount(min_count=2),
            WriteFileBeforeCurlPost(),
            UsesEnvCredentials(),
            HitsExpectedEndpoint(),
        ],
    )


async def _task(inputs: dict[str, Any]) -> AgentRun:
    spec: AgentSpec = inputs["agent"]
    script: list[ScriptStep] = inputs["script"]
    return await run_scripted(spec.system_prompt, script, PLATFORM_TOOLS)


@pytest.mark.asyncio
async def test_evaluators_catch_known_failures():
    """Ensure evaluators correctly distinguish good vs bad scripted runs."""
    dataset = _build_dataset()
    report = await dataset.evaluate(_task, max_concurrency=4)

    by_name = {case.name: case for case in report.cases}

    # Good cases pass everything.
    for good in ("content_good", "analyst_good", "qa_good"):
        scores = {a.name: a.value for a in by_name[good].assertions.values()}
        failed = {k: v for k, v in scores.items() if v is False}
        assert not failed, f"{good} should pass all evaluators, failed: {failed}"

    # Anti-patterns get flagged by the right evaluator.
    inline = {a.name: a.value for a in by_name["content_bad_inline_json"].assertions.values()}
    assert inline["WriteFileBeforeCurlPost"] is False, "inline JSON must fail WriteFileBeforeCurlPost"

    done_only = {a.name: a.value for a in by_name["content_bad_done_only"].assertions.values()}
    assert done_only["CompletedTask"] is False, "'Done.' response must fail CompletedTask"
    assert done_only["MinExecuteCount"] is False, "single execute must fail MinExecuteCount"
    assert done_only["HitsExpectedEndpoint"] is False, "missing /blog/posts call must fail HitsExpectedEndpoint"

    hardcoded = {a.name: a.value for a in by_name["content_bad_hardcoded_key"].assertions.values()}
    assert hardcoded["UsesEnvCredentials"] is False, "hardcoded API key must fail UsesEnvCredentials"


if __name__ == "__main__":
    # Convenience: `python -m tests.evals.test_hosted_agents` prints a report.
    dataset = _build_dataset()
    report = asyncio.run(dataset.evaluate(_task, max_concurrency=4))
    report.print(include_input=False, include_output=False, include_durations=True)
