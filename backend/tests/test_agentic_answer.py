"""Unit tests for app.services.agentic_answer -- the sandboxed-agent drive.

Mocked httpx transport, no Docker/testcontainers: the property under test is
the module's OWN behaviour (step persistence order, cleanup guarantee, budget
handling), not agent-runner's or a real sandbox's, so a real container would
only add latency and flakiness without covering anything new.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial

import httpx
import pytest

from app.core import config as config_module
from app.services.agentic_answer import (
    SOFT_DEADLINE_MARGIN_SECONDS,
    TIMEOUT_PARTIAL_MARKER,
    AgenticAnswerRequest,
    AgenticDriveError,
    AgentStep,
    run_agentic_answer,
)


def _ndjson_response(lines: list[dict]) -> bytes:
    return "\n".join(json.dumps(line) for line in lines).encode() + b"\n"


def _request(**overrides) -> AgenticAnswerRequest:
    defaults = dict(
        battle_id="b1",
        side_value="a",
        task_prompt="do the task",
        system_prompt="answer tersely",
        wire_model="glm-4.5",
        provider_base_url="https://api.z.ai",
        provider_api_key="pk-test-provider",
        fallback_base_url="https://openrouter.ai/api/v1",
        fallback_api_key="pk-test-fallback",
        max_steps=12,
        answer_seconds=530.0,
    )
    defaults.update(overrides)
    return AgenticAnswerRequest(**defaults)


@pytest.fixture(autouse=True)
def _configure_runner_url(monkeypatch):
    config_module.get_settings.cache_clear()
    monkeypatch.setenv("AGENT_RUNNER_URL", "http://runner.test")
    monkeypatch.setenv("AGENT_RUNNER_KEY", "test-key")
    yield
    config_module.get_settings.cache_clear()


def _handler(
    events: list[dict],
    stop_calls: list[str],
    start_calls: list[str],
    starts: list[dict] | None = None,
):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/start"):
            start_calls.append(path)
            if starts is not None:
                starts.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "running"})
        if path.endswith("/stop"):
            stop_calls.append(path)
            return httpx.Response(200, json={"status": "stopped"})
        if path.endswith("/chat/stream"):
            return httpx.Response(200, content=_ndjson_response(events))
        raise AssertionError(f"unexpected path {path}")

    return handle


@pytest.mark.asyncio
async def test_persists_steps_as_they_arrive_and_returns_final_reply(monkeypatch):
    events = [
        {"type": "tool_call", "tool_name": "search", "args": {"q": "x"}},
        {"type": "tool_result", "tool_name": "search", "output": "result text"},
        {"type": "text_delta", "content": "ignored fragment"},
        {"type": "done", "reply": "the final answer", "tool_calls": [], "thinking": None},
    ]
    start_calls: list[str] = []
    stop_calls: list[str] = []
    transport = httpx.MockTransport(_handler(events, stop_calls, start_calls))
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    recorded: list[AgentStep] = []

    async def on_step(step: AgentStep) -> None:
        recorded.append(step)

    result = await run_agentic_answer(_request(), on_step)

    assert result == "the final answer"
    assert [s.is_final for s in recorded] == [False, False, True]
    assert recorded[0].content.startswith("[tool_call]")
    assert recorded[1].content.startswith("[tool_result]")
    assert recorded[-1].content == "the final answer"
    assert not any(s.timed_out for s in recorded)
    # STRICTLY increasing, not merely sorted: battle_submissions is keyed
    # (battle_id, side, seq_no), so two steps sharing a number collide and the
    # second write is refused. A sorted-only assertion passes [2, 3, 3] happily,
    # which is exactly the shape that lost the final answer in the first live run.
    seq_nos = [s.seq_no for s in recorded]
    assert seq_nos == sorted(set(seq_nos)), f"seq_no must be unique per side: {seq_nos}"
    assert start_calls and stop_calls  # container started AND stopped


@pytest.mark.asyncio
async def test_task_prompt_carries_the_seconds_and_step_budget(monkeypatch):
    seen_prompts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/start"):
            return httpx.Response(200, json={"status": "running"})
        if path.endswith("/stop"):
            return httpx.Response(200, json={"status": "stopped"})
        if path.endswith("/chat/stream"):
            body = json.loads(request.content)
            seen_prompts.append(body["content"])
            events = [{"type": "done", "reply": "ok", "tool_calls": [], "thinking": None}]
            return httpx.Response(200, content=_ndjson_response(events))
        raise AssertionError("unexpected path")

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    async def on_step(step: AgentStep) -> None:
        pass

    await run_agentic_answer(_request(answer_seconds=530.0, max_steps=7), on_step)

    assert len(seen_prompts) == 1
    assert "530 seconds" in seen_prompts[0]
    assert "7 tool steps" in seen_prompts[0]
    assert "do the task" in seen_prompts[0]


@pytest.mark.asyncio
async def test_sandbox_start_carries_the_contenders_own_system_prompt(monkeypatch):
    starts: list[dict] = []
    events = [{"type": "done", "reply": "ok", "tool_calls": [], "thinking": None}]
    transport = httpx.MockTransport(_handler(events, [], [], starts))
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    async def on_step(step: AgentStep) -> None:
        pass

    await run_agentic_answer(_request(system_prompt="be a ruthless pirate negotiator"), on_step)

    assert len(starts) == 1
    assert "be a ruthless pirate negotiator" in starts[0]["system_prompt"]


@pytest.mark.asyncio
async def test_openrouter_contender_uses_fallback_creds_when_provider_empty(monkeypatch):
    starts: list[dict] = []
    events = [{"type": "done", "reply": "ok", "tool_calls": [], "thinking": None}]
    transport = httpx.MockTransport(_handler(events, [], [], starts))
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    async def on_step(step: AgentStep) -> None:
        pass

    # resolve_provider() returns None for OpenRouter contenders -> caller
    # passes empty provider_base_url/api_key, same as battle_runner.py does.
    request = _request(provider_base_url="", provider_api_key="")
    await run_agentic_answer(request, on_step)

    assert starts[0]["provider_base_url"] == request.fallback_base_url
    assert starts[0]["provider_api_key"] == request.fallback_api_key


@pytest.mark.asyncio
async def test_keyless_provider_never_borrows_the_fallback_credential(monkeypatch):
    """llm7 resolves with a real base_url and a legitimately EMPTY api_key
    (key_optional). The `or` idiom that is correct for the OpenRouter case
    (both fields empty) is WRONG here: it would fall through to the caller's
    own judge/platform credential and send it to api.llm7.io, an unrelated
    third-party host (CRITICAL finding 1).
    """
    starts: list[dict] = []
    events = [{"type": "done", "reply": "ok", "tool_calls": [], "thinking": None}]
    transport = httpx.MockTransport(_handler(events, [], [], starts))
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    async def on_step(step: AgentStep) -> None:
        pass

    request = _request(
        provider_base_url="https://api.llm7.io/v1",
        provider_api_key="",
        fallback_api_key="pk-platform-judge-key",
    )
    await run_agentic_answer(request, on_step)

    assert starts[0]["provider_base_url"] == "https://api.llm7.io/v1"
    assert starts[0]["provider_api_key"] == ""
    assert starts[0]["provider_api_key"] != "pk-platform-judge-key"


@pytest.mark.asyncio
async def test_soft_deadline_never_puts_the_marker_string_in_content(monkeypatch):
    """A stream that never sends 'done' before the soft deadline still leaves
    every step already persisted, plus one synthetic timed_out step whose
    CONTENT is the draft buffer -- never TIMEOUT_PARTIAL_MARKER, which is an
    error label, not gradeable answer text (BLOCKING 1 from review).

    Mutation check: delete the soft-deadline branch in _stream_agent_turn and
    this test hangs/times out instead of completing -- it cannot pass by
    accident.
    """
    recorded: list[AgentStep] = []

    async def on_step(step: AgentStep) -> None:
        recorded.append(step)
        if not step.timed_out:
            # Simulate real per-step latency so the soft deadline (which is
            # shorter than the total stream) actually has time to fire.
            await asyncio.sleep(0.05)

    async def infinite_events():
        # A stream that keeps emitting tool_call events and never reaches
        # 'done' -- models an agent that is still working when time runs out.
        i = 0
        while True:
            yield json.dumps({"type": "tool_call", "tool_name": "loop", "args": {"i": i}}) + "\n"
            i += 1
            await asyncio.sleep(0.05)

    class _FakeStreamResponse:
        status_code = 200

        async def aiter_lines(self):
            async for line in infinite_events():
                yield line

        async def aread(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, **kw):
            pass

        async def post(self, url, **kw):
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("POST", url))

        def stream(self, method, url, **kw):
            return _FakeStreamResponse()

        async def aclose(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    # answer_seconds tiny so SOFT_DEADLINE_MARGIN_SECONDS clamps the soft
    # deadline to "now" (max(0, tiny - margin)) -- fires on the first check.
    request = _request(answer_seconds=1.0, max_steps=50)
    assert SOFT_DEADLINE_MARGIN_SECONDS > request.answer_seconds  # sanity on the fixture

    result = await run_agentic_answer(request, on_step)

    assert result is None  # no 'done' event was ever reached
    assert recorded, "steps produced before the cutoff must be persisted"
    assert recorded[-1].timed_out is True
    assert recorded[-1].content != TIMEOUT_PARTIAL_MARKER
    assert not any(s.is_final and not s.timed_out for s in recorded)


@pytest.mark.asyncio
async def test_soft_deadline_preserves_drafted_text_as_content(monkeypatch):
    """A text_delta emitted before the cutoff becomes the timeout step's
    content -- partial work must survive, not be replaced by the marker."""

    class _FakeStreamResponse:
        status_code = 200

        async def aiter_lines(self):
            yield json.dumps({"type": "text_delta", "content": "The answer is "})
            yield json.dumps({"type": "text_delta", "content": "42"})
            # Never emits 'done' -- soft deadline must cut here.
            while True:
                await asyncio.sleep(0.05)

        async def aread(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, **kw):
            pass

        async def post(self, url, **kw):
            return httpx.Response(200, json={"status": "ok"}, request=httpx.Request("POST", url))

        def stream(self, method, url, **kw):
            return _FakeStreamResponse()

        async def aclose(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    recorded: list[AgentStep] = []

    async def on_step(step: AgentStep) -> None:
        recorded.append(step)

    # Just over the soft-deadline margin, so the loop gets one pass to drain
    # the two already-available lines before its next remaining<=0 check.
    request = _request(answer_seconds=SOFT_DEADLINE_MARGIN_SECONDS + 0.2, max_steps=50)
    await run_agentic_answer(request, on_step)

    assert recorded[-1].timed_out is True
    assert recorded[-1].content == "The answer is 42"


@pytest.mark.asyncio
async def test_container_stopped_even_when_stream_raises(monkeypatch):
    start_calls: list[str] = []
    stop_calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/start"):
            start_calls.append(request.url.path)
            return httpx.Response(200, json={"status": "running"})
        if request.url.path.endswith("/stop"):
            stop_calls.append(request.url.path)
            return httpx.Response(200, json={"status": "stopped"})
        if request.url.path.endswith("/chat/stream"):
            return httpx.Response(500, content=b"boom")
        raise AssertionError("unexpected path")

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    async def on_step(step: AgentStep) -> None:
        pass

    with pytest.raises(AgenticDriveError):
        await run_agentic_answer(_request(side_value="b"), on_step)

    assert start_calls
    assert stop_calls  # cleanup ran despite the stream failure


@pytest.mark.asyncio
async def test_container_stopped_even_when_drive_is_cancelled(monkeypatch):
    """Cancelling the caller (as ANSWER_DRIVE_BUDGET_SECONDS's wait_for does on
    timeout) must not leak the container.

    HONEST LIMIT: this passed even against a same-client (unshielded) mutant
    tried during review -- httpx.MockTransport does not reproduce the
    transport-level cancelled-scope state that makes a same-client cleanup
    POST raise CancelledError on real sockets. It verifies the happy path
    (stop is reached and called after cancellation) and guards against a
    regression that removes the call entirely; it does NOT prove the shield
    is what saves it. The shield is correct by construction (a fresh client
    has no cancelled scope to inherit), not by this test's own mutation
    coverage.
    """
    stop_calls: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/start"):
            return httpx.Response(200, json={"status": "running"})
        if path.endswith("/stop"):
            stop_calls.append(path)
            return httpx.Response(200, json={"status": "stopped"})
        if path.endswith("/chat/stream"):
            # Never resolves before the test cancels the driving task --
            # models a stream call in flight when the caller times out.
            await asyncio.sleep(100)
            raise AssertionError("unreachable")
        raise AssertionError(f"unexpected path {path}")

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    async def on_step(step: AgentStep) -> None:
        pass

    task = asyncio.ensure_future(run_agentic_answer(_request(answer_seconds=100.0), on_step))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert stop_calls, "container must be stopped even when the drive itself is cancelled"


@pytest.mark.asyncio
async def test_step_ceiling_caps_recorded_steps_but_final_always_lands(monkeypatch):
    events = [{"type": "tool_call", "tool_name": "t", "args": {}} for _ in range(5)]
    events.append({"type": "done", "reply": "ok", "tool_calls": [], "thinking": None})
    transport = httpx.MockTransport(_handler(events, [], []))
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    recorded: list[AgentStep] = []

    async def on_step(step: AgentStep) -> None:
        recorded.append(step)

    result = await run_agentic_answer(_request(max_steps=2), on_step)

    assert result == "ok"
    non_final = [s for s in recorded if not s.is_final]
    assert len(non_final) == 2
    assert recorded[-1].is_final


@pytest.mark.asyncio
async def test_tool_result_output_is_capped_and_sanitized(monkeypatch):
    huge_output = "x" * 50_000
    events = [
        {"type": "tool_result", "tool_name": "fetch", "output": huge_output},
        {"type": "done", "reply": "ok", "tool_calls": [], "thinking": None},
    ]
    transport = httpx.MockTransport(_handler(events, [], []))
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport))

    recorded: list[AgentStep] = []

    async def on_step(step: AgentStep) -> None:
        recorded.append(step)

    await run_agentic_answer(_request(), on_step)

    tool_step = recorded[0]
    assert len(tool_step.content) < len(huge_output)
