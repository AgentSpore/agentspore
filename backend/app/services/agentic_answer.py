"""Agentic contender path: a battle side answered by a real sandboxed agent.

Unlike ``_answer_with_model`` (battle_runner.py, ONE HTTP call), this drives a
sandbox: agent-runner starts a container pinned to the contender's
provider+model, the task is sent as a chat turn, and the NDJSON stream
(agent-runner/routes/chat.py:346) becomes a sequence of ``battle_submissions``
rows persisted AS THEY ARRIVE — a battle that dies mid-run must still show
what the agent had done. The container is destroyed in ``finally`` on every
exit path — a leaked container is the failure mode this module guards against.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass

import httpx
from loguru import logger

from app.core.config import get_settings
from app.services.battle_judges import sanitize_submission

# NDJSON event types that represent one recordable step of the agent's path,
# mapped to a short label used when the step is rendered for the judge/page.
# text_delta fragments are buffered separately (see _StreamState.draft_reply)
# rather than persisted as their own steps — they are pieces of the SAME final
# answer, and the buffer is what a soft-deadline cutoff falls back to.
_STEP_EVENT_LABELS = {
    "tool_call": "tool_call",
    "tool_result": "tool_result",
}

# Gap between the SOFT warning in the prompt and the hard cutoff below. Must
# fit one whole agent step, or the warning is decorative: the agent reads it,
# starts one more tool call, and the cutoff lands mid-step. 90s covers the
# slowest step observed (a full task answer ~120s end-to-end,
# DEMO_ANSWER_TIMEOUT_SECONDS in battle_runner.py:257) plus margin for the
# extra tool round-trip an agent step adds over a plain completion.
SOFT_DEADLINE_MARGIN_SECONDS = 90.0

# The ERROR label for a soft-deadline cutoff — never placed in `content`. The
# CONTENT of a timed-out step is whatever draft text was actually buffered (or
# empty). Putting the marker string in content would make an empty-handed
# timeout read as a real, gradeable answer to the judge (the bug this constant
# exists to prevent) instead of a forfeit-worthy silence.
TIMEOUT_PARTIAL_MARKER = "agentic drive timed out"

# Read timeout on the chat/stream HTTP call. Bounded to the SOFT deadline plus
# one grace window, not a flat constant — a flat 300s can outlive the soft
# deadline on a short answer_seconds battle, so httpx.ReadTimeout would land
# BEFORE the soft-deadline loop ever gets to run its own check, and the caller
# sees AgenticDriveError (-> record_unreachable/void) for what is actually an
# ordinary timeout. The grace covers one NDJSON line already in flight.
_STREAM_READ_GRACE_SECONDS = 30.0


class AgenticDriveError(Exception):
    """The sandbox could not be started or the stream could not be read.

    Mirrors JudgeTransportError's role for the model path: raising here (rather
    than returning None) lets the caller record "provider unreachable" instead
    of silently forfeiting a side that was never actually run.
    """


@dataclass(frozen=True)
class AgentStep:
    """One recorded step of an agentic contender's path, in arrival order.

    ``content`` is ALWAYS what the judge/page may legitimately read as this
    step's text — a tool-call/result summary, the agent's real final reply, or
    (for a ``timed_out`` row) whatever draft text was buffered before the cut,
    possibly empty. It never carries :data:`TIMEOUT_PARTIAL_MARKER` — that
    string is a caller-side ERROR label, not gradeable content.

    ``timed_out`` is True only for the synthetic final row the soft deadline
    writes, distinct from an ordinary final row the agent produced itself.
    """

    seq_no: int
    content: str
    is_final: bool
    timed_out: bool = False


@dataclass(frozen=True)
class AgenticAnswerRequest:
    """One sandboxed agent turn's inputs, bundled under the 5-param budget.

    ``system_prompt`` is the contender's OWN approach (battle_contenders row),
    not a fixed generic framing — two contenders on the same model with
    different prompts must stay two different fighters. ``fallback_base_url``/
    ``fallback_api_key`` are the caller's judge/OpenRouter credentials, used
    ONLY when ``provider_base_url``/``provider_api_key`` are empty (OpenRouter
    contenders: resolve_provider() returns None for them by design).

    ``answer_seconds`` — the WHOLE turn's wall-clock budget, told to the agent
    verbatim (_with_budget_instructions). Must stay below
    ANSWER_DRIVE_BUDGET_SECONDS (battle_runner.py:268) minus sandbox overhead.
    """

    battle_id: str
    side_value: str
    task_prompt: str
    system_prompt: str
    wire_model: str
    provider_base_url: str
    provider_api_key: str
    fallback_base_url: str
    fallback_api_key: str
    max_steps: int
    answer_seconds: float

    def resolved_credentials(self) -> tuple[str, str]:
        """The provider endpoint to start the sandbox against, and its credential.

        resolve_provider() returns None for OpenRouter by design — it is the
        DEFAULT route, not an "extra" provider — so a contender on an OpenRouter
        model arrives here with both provider fields empty. The caller's own
        credentials are the fallback, mirroring what the model path does in
        battle_runner._answer_with_model. Without this an OpenRouter contender
        starts with no provider and voids every battle it enters, reported as
        "unreachable" rather than as the misconfiguration it is.

        Branches on ``provider_base_url``, NOT on the key: a resolved keyless
        provider (llm7, key_optional) has a real base_url and a legitimately
        empty api_key, a state the old `key or fallback_key` idiom could not
        express — it fell through and sent the CALLER's credential (a
        different provider's key) to the resolved provider's host. Only the
        genuinely unresolved case (base_url empty, as resolve_provider()
        returns for OpenRouter) falls back to the caller's own pair.
        """
        if self.provider_base_url:
            return (self.provider_base_url, self.provider_api_key)
        return (self.fallback_base_url, self.fallback_api_key)


@dataclass(frozen=True)
class _Sandbox:
    """Runner call target for one battle side's container, bundled under the
    5-param budget."""

    client: httpx.AsyncClient
    runner_url: str
    headers: dict
    container_id: str


async def run_agentic_answer(request: AgenticAnswerRequest, on_step) -> str | None:
    """Drive one sandboxed agent turn, calling ``on_step`` for each path step.

    ``on_step(AgentStep)`` is awaited for every step, including the final one —
    persistence is the caller's concern, this function owns the container, the
    stream and the wall clock.

    The agent is told its own budget (``answer_seconds``, ``max_steps``) in the
    task prompt — the only channel available, since agent-runner serialises one
    chat turn per session (agent-runner/session.py:119, chat_lock) and a
    mid-stream message cannot be injected. A SOFT deadline
    (``answer_seconds - SOFT_DEADLINE_MARGIN_SECONDS``) then cuts the stream
    early, persisting one synthetic timeout step. The HARD deadline is the
    caller's own ``ANSWER_DRIVE_BUDGET_SECONDS`` wait_for, not duplicated here.

    Returns the final reply, or None if nothing was produced before either
    deadline. Raises :class:`AgenticDriveError` if the sandbox never started or
    the stream never produced a terminal/timeout event.
    """
    settings = get_settings()
    if not settings.agent_runner_url:
        raise AgenticDriveError("agent runner URL not configured")

    container_id = f"battle-{request.battle_id}-{request.side_value}-{uuid.uuid4().hex[:8]}"
    headers = {"X-Runner-Key": settings.agent_runner_key} if settings.agent_runner_key else {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        sandbox = _Sandbox(client, settings.agent_runner_url, headers, container_id)
        await _start_sandbox(sandbox, request)
        try:
            return await _stream_agent_turn(sandbox, request, on_step)
        finally:
            # The client used for the stream sits in a scope that CancelledError
            # can be unwinding — awaiting a POST on it here would raise the same
            # CancelledError right back, and the container is never stopped.
            # A fresh client + shield survives that: shield lets THIS coroutine
            # keep running even though its caller was cancelled, and the new
            # client has no cancelled scope to inherit the exception from.
            await _stop_sandbox_shielded(settings.agent_runner_url, headers, container_id)


async def _start_sandbox(sandbox: _Sandbox, request: AgenticAnswerRequest) -> None:
    # provider_base_url/api_key are empty for an OpenRouter contender —
    base_url, credential = request.resolved_credentials()
    try:
        resp = await sandbox.client.post(
            f"{sandbox.runner_url}/agents/{sandbox.container_id}/start",
            json={
                "agent_id": sandbox.container_id,
                "system_prompt": _framed_system_prompt(request.system_prompt),
                "model": request.wire_model,
                "provider_base_url": base_url,
                "provider_api_key": credential,
                "heartbeat_seconds": 0,
            },
            headers=sandbox.headers,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AgenticDriveError(f"sandbox start failed: {exc}") from exc


# Cleanup gets a SHORT timeout of its own — a hung stop call must not extend
# how long a cancelled/errored drive holds the caller up.
_STOP_TIMEOUT_SECONDS = 15.0


async def _stop_sandbox_shielded(runner_url: str, headers: dict, container_id: str) -> None:
    try:
        await asyncio.shield(_stop_sandbox(runner_url, headers, container_id))
    except asyncio.CancelledError:
        # The shield protects the coroutine from the OUTER cancellation, but if
        # cleanup itself is what gets cancelled (process shutdown), there is
        # nothing left to do — the orphan reaper is the backstop.
        logger.warning("battle sandbox {} stop was cancelled mid-cleanup", container_id)
    except Exception as exc:
        logger.warning("battle sandbox {} stop failed: {}", container_id, exc)


async def _stop_sandbox(runner_url: str, headers: dict, container_id: str) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(_STOP_TIMEOUT_SECONDS)) as client:
        resp = await client.post(f"{runner_url}/agents/{container_id}/stop", headers=headers)
        resp.raise_for_status()


@dataclass
class _StreamState:
    """Mutable progress of one stream read, threaded through the event loop.

    ``draft_reply`` accumulates text_delta fragments — the answer the agent is
    mid-way through writing. A soft-deadline cutoff falls back to it as
    content, so a timeout with real partial work reads differently than a
    timeout with nothing produced at all.
    """

    seq_no: int = 1
    steps_recorded: int = 0
    final_reply: str | None = None
    draft_reply: str = ""


async def _stream_agent_turn(
    sandbox: _Sandbox, request: AgenticAnswerRequest, on_step
) -> str | None:
    soft_deadline = time.monotonic() + max(
        0.0, request.answer_seconds - SOFT_DEADLINE_MARGIN_SECONDS
    )
    read_timeout = max(0.0, request.answer_seconds) + _STREAM_READ_GRACE_SECONDS
    prompt = _with_budget_instructions(request)
    state = _StreamState()
    try:
        async with sandbox.client.stream(
            "POST",
            f"{sandbox.runner_url}/agents/{sandbox.container_id}/chat/stream",
            json={"content": prompt},
            headers=sandbox.headers,
            timeout=httpx.Timeout(read_timeout, connect=10.0),
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise AgenticDriveError(f"stream start failed: {body.decode()[:200]}")
            lines = response.aiter_lines()
            while True:
                remaining = soft_deadline - time.monotonic()
                if remaining <= 0:
                    return await _cut_for_timeout(state, on_step)
                try:
                    line = await asyncio.wait_for(lines.__anext__(), timeout=remaining)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break
                done = await _handle_line(line, request.max_steps, state, on_step)
                if done:
                    return state.final_reply or None
    except httpx.ReadTimeout:
        # A soft deadline shorter than the HTTP read timeout would otherwise
        # let httpx race ahead and raise here first — treated as the SAME
        # timeout outcome as the soft-deadline branch above, never as
        # "provider unreachable" (the model DID answer, up to this point).
        return await _cut_for_timeout(state, on_step)
    except httpx.HTTPError as exc:
        raise AgenticDriveError(f"stream failed: {exc}") from exc
    if state.final_reply is None:
        raise AgenticDriveError("stream ended without a terminal event")
    return state.final_reply


async def _cut_for_timeout(state: _StreamState, on_step) -> str | None:
    """Persist the soft-deadline cutoff. Content is the DRAFT buffer (possibly
    empty), never :data:`TIMEOUT_PARTIAL_MARKER` — that string is the error
    label the caller attaches separately, not gradeable answer text."""
    state.seq_no += 1
    await on_step(AgentStep(state.seq_no, state.draft_reply, True, timed_out=True))
    return state.final_reply


async def _handle_line(line: str, max_steps: int, state: _StreamState, on_step) -> bool:
    """Apply one NDJSON line to ``state``. Returns True when the turn is done."""
    if not line.strip():
        return False
    event = _parse_event(line)
    if event is None:
        return False
    event_type = event.get("type")
    if event_type == "text_delta":
        state.draft_reply += str(event.get("content") or "")
        return False
    if event_type == "done":
        state.final_reply = str(event.get("reply") or "")
        # Its OWN seq_no: battle_submissions is keyed (battle_id, side, seq_no),
        # so reusing the last step's number makes the final row collide with it
        # and add_submission refuses the write. The answer would then be missing
        # from a battle whose path looks complete.
        state.seq_no += 1
        await on_step(AgentStep(state.seq_no, state.final_reply, is_final=True))
        return True
    if event_type == "error":
        raise AgenticDriveError(str(event.get("message") or "agent error"))
    label = _STEP_EVENT_LABELS.get(event_type)
    if label is None or state.steps_recorded >= max_steps:
        return False
    text = _format_step(label, event)
    if not text:
        return False
    state.seq_no += 1
    state.steps_recorded += 1
    await on_step(AgentStep(state.seq_no, text, is_final=False))
    return False


def _parse_event(line: str) -> dict | None:
    try:
        return json.loads(line)
    except (json.JSONDecodeError, TypeError):
        logger.debug("battle agentic drive: unparsable ndjson line ignored")
        return None


# Per-step cap. A single tool (a fetch, a file read) can return megabytes, and
# this text lands on the PUBLIC battle page — capped and sanitized here, not
# left to whatever downstream truncation exists further along the pipe.
_STEP_MAX_CHARS = 2_000


def _format_step(label: str, event: dict) -> str:
    if label == "tool_call":
        raw = f"[tool_call] {event.get('tool_name', '?')}({event.get('args', {})})"
    else:
        raw = f"[tool_result] {event.get('tool_name', '?')} -> {event.get('output', '')}"
    cleaned, _truncated = sanitize_submission(raw, max_chars=_STEP_MAX_CHARS)
    return cleaned


def _with_budget_instructions(request: AgenticAnswerRequest) -> str:
    """Prepend the agent's own time/step budget to the task, in plain terms.

    Both sides get the SAME wording from the SAME fields — an asymmetric
    budget would be a hidden handicap the verdict could never surface.
    """
    seconds = int(request.answer_seconds)
    return (
        f"You have {seconds} seconds and at most {request.max_steps} tool steps "
        "for this ENTIRE task, including your final answer. Budget them yourself: "
        "an unfinished answer written in time beats a complete one that never "
        "arrives. If you are close to either limit, stop exploring and write "
        "your final answer now.\n\n"
        f"{request.task_prompt}"
    )


def _framed_system_prompt(contender_system_prompt: str) -> str:
    """The contender's OWN approach, wrapped in the fixed timed-battle framing.

    A contender is (model, approach) — battle_runner.py's docstring on
    drive_contender_submission. Sending only the fixed framing (dropping the
    contender's system_prompt) collapses every agentic contender on one model
    into the same fighter regardless of its configured approach.
    """
    return f"{_AGENTIC_FIGHTER_FRAMING}\n\n{contender_system_prompt}"


_AGENTIC_FIGHTER_FRAMING = (
    "You are competing in a timed one-shot task. Use your tools as needed, "
    "then give your final answer. You will not get another turn."
)
