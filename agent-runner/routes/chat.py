"""Chat endpoints: chat (non-streaming) and chat/stream.

Phase 2+3: per-session isolation via WorkerPool.

When owner_session_id is provided and max_concurrent_sessions > 1:
  - Get-or-create a SessionWorker for that session_id
  - Acquire executor_semaphore slot (cross-session concurrency limit)
  - Acquire session-scoped lock (within-session serialization)
  - Use session-scoped message_history
  - Release slot + lock in finally

When max_concurrent_sessions == 1 (default) or no owner_session_id:
  - Fall through to legacy global chat_lock behavior (zero regression)
"""

import asyncio
import json
import random
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic_ai import DeferredToolRequests, FunctionToolResultEvent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import PartStartEvent, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.tools import DeferredToolResults

from config import get_settings
from helpers import _extract_response
from llm_fallback import _load_model_chain
from observability import use_agent_context
from replay_sampler import maybe_sample
from sandbox import is_command_safe
from schemas import ChatRequest, ChatResponse
from session import sanitize_history, sessions
from session_worker import SessionWorker

settings = get_settings()

router = APIRouter()


# Transient upstream LLM failures: OpenRouter/Nemotron sometimes return 200 OK with
# a NULL body (id/choices/model/object all None) → pydantic ChatCompletion validation
# raises ValidationError → bubbled up as "Invalid response from openai chat
# completions endpoint" or similar. These are flaky upstream issues that retry fixes.
_TRANSIENT_LLM_ERROR_MARKERS: tuple[str, ...] = (
    "Invalid response from",
    "validation errors for ChatCompletion",
    "validation error for ChatCompletion",
    "Input should be a valid",
    "503 Service Unavailable",
    # pydantic-ai renders an upstream error as "status_code: NNN, model_name: ...",
    # never as httpx's status line, so the marker above never matched a real 503.
    # Measured 2026-08-26 in production: 4 of 9 failures in six hours carried
    # `status_code: 503 ... 'code': 'model_temporarily_unavailable'` and were
    # classified permanent, so neither retry nor fallover ever ran on them.
    "model_temporarily_unavailable",
    "502 Bad Gateway",
    "504 Gateway Timeout",
    # Rate limiting. Z.AI's free GLM tier serves only ~3 concurrent requests
    # (measured 2026-07-15: 6 parallel calls → 3×200, 3×429 code 1302); the
    # 4th+ caller gets 429 until an in-flight request finishes. Since Z.AI is
    # the only provider our hosts can reach, backing off IS the resilience —
    # there is no second provider to fall over to.
    # Markers are deliberately specific — a bare "429" substring would also
    # match unrelated digits (token counts, ids) and retry non-transient errors.
    "Error code: 429",      # openai SDK RateLimitError repr
    "429 Too Many Requests",  # httpx status-line repr
    "Too Many Requests",
    "Rate limit reached",   # Z.AI error code 1302 message text
    "rate_limit_exceeded",  # OpenAI-compatible error.code
    # llm7.io's per-key rate limiter. Confirmed transient by measurement
    # (2026-08-23): the same key's retry_after cycled 3 -> 39 -> 84297 -> 39
    # seconds over one session, i.e. a moving throttle window, not a fixed
    # daily ban. "insufficient_quota"/"quota_exceeded" alone are NOT used as
    # markers here — OpenAI reports a permanent zero-balance error with the
    # identical `'type': 'insufficient_quota'` (measured 2026-08-26), so only
    # "Daily token quota exceeded" is checked: it is llm7's unique wording and
    # sufficient by itself to identify llm7's throttle.
    "Daily token quota exceeded",
    # llm7.io reports a model being temporarily unreachable as HTTP 400
    # code "model_unavailable" (measured 2026-08-26 against the live gateway):
    # {"error": {"message": "Model 'DeepSeek-V4-Flash-0731' is currently
    # unavailable.", "type": "invalid_request_error", "code":
    # "model_unavailable"}}. The model comes back later, so this is transient
    # despite the 400 status — retry and fallover apply. Narrow to the `code`
    # field's value so it never matches unrelated 400s such as
    # "missing_api_key" or a generic invalid_request_error.
    "model_unavailable",
)

# Errors that arrive with HTTP 429 but are PERMANENT, so they must never be
# retried. Z.AI reports a zero balance as 429 code 1113 ("Insufficient balance
# or no resource package"), which the openai SDK renders as
# "Error code: 429 - {'error': {'code': '1113', ...}}" — indistinguishable from
# a rate limit by HTTP status alone. Only the JSON `code` field separates "slow
# down and try again" (1302) from "this account cannot pay" (1113). Retrying
# 1113 would burn the whole backoff on a request that can never succeed.
# Checked BEFORE the transient markers, which is what makes them precise.
_PERMANENT_LLM_ERROR_MARKERS: tuple[str, ...] = (
    "'code': '1113'",
    '"code": "1113"',
    "'code': 1113",
    '"code": 1113',
    "Insufficient balance",
    # OpenAI's zero-balance 429 also matches the generic "Error code: 429"
    # transient marker (measured 2026-08-26); its billing-exhausted wording is
    # unique enough to check for directly rather than trying to keep every
    # transient 429 marker from ever matching it.
    "exceeded your current quota",
)

# Markers indicating the conversation history has an illegal shape for the
# current model provider.  These errors are not transient (retrying with the
# same history will always fail), so they are handled separately: history is
# cleared before the retry rather than retrying as-is.
_HISTORY_SHAPE_ERROR_MARKERS: tuple[str, ...] = (
    "messages parameter is illegal",
    "1214",
)

# Fraction of settings.chat_timeout honored as a provider's retry_after inside
# _run_with_llm_retry, rather than a fixed number: chat_timeout is configurable
# and differs from its 120s repo default in production (CHAT_TIMEOUT=600,
# see docker-compose.yml). A fixed 120.0 cap equal to a 120s timeout let 4
# retries burn 3*120s = 360s of sleep alone under the held chat_lock. One
# third leaves room for the retry itself plus the remaining attempts within
# whatever timeout is actually configured.
_RETRY_AFTER_CAP_FRACTION = 1 / 3


def _is_transient_llm_error(exc: Exception) -> bool:
    """Return True if an exception is a flaky upstream LLM response worth retrying.

    A permanent provider error wins over the transient markers: HTTP 429 covers
    both "rate limited" (retry) and "insufficient balance" (hopeless), so the
    JSON error code decides.
    """
    msg = str(exc)
    if any(marker in msg for marker in _PERMANENT_LLM_ERROR_MARKERS):
        return False
    return any(marker in msg for marker in _TRANSIENT_LLM_ERROR_MARKERS)


def _is_history_shape_error(exc: Exception) -> bool:
    """Return True if the error is caused by illegal conversation history shape.

    Z.AI error 1214 ("messages parameter is illegal") falls in this category.
    The fix is to clear / trim message_history before retrying, NOT to retry
    the same request unchanged.
    """
    msg = str(exc)
    return any(marker in msg for marker in _HISTORY_SHAPE_ERROR_MARKERS)


def _extract_retry_after_seconds(exc: Exception) -> float | None:
    """Return the provider-reported retry_after seconds, if present and usable.

    Only ModelHTTPError carries a structured JSON body (Z.AI/llm7 embed
    retry_after there); other transient markers (502/503/504 strings) never
    have one, so this returns None for them and the caller falls back to the
    existing exponential backoff.

    The openai SDK unwraps the `{"error": {...}}` envelope before setting
    `exc.body` (measured 2026-08-26 against a live llm7 429: body keys were
    `['message', 'type', 'param', 'code', 'retry_after']`, no 'error' wrapper),
    so retry_after is read from the top level first, falling back to a nested
    'error' dict for gateways that don't unwrap it.
    """
    if not isinstance(exc, ModelHTTPError) or not isinstance(exc.body, dict):
        return None
    nested = exc.body.get("error")
    src = nested if isinstance(nested, dict) else exc.body
    retry_after = src.get("retry_after")
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    return None


async def _run_with_llm_retry(
    coro_factory, *, max_attempts: int = 4, base_delay: float = 1.0, deadline: float | None = None,
):
    """Invoke an async agent.run() coroutine, retrying on transient upstream LLM errors.

    coro_factory: callable that returns a fresh coroutine (NOT a bare coroutine — those
    cannot be re-awaited after a failure). Pass `lambda: session.agent.run(...)`.

    deadline: optional time.monotonic() cutoff (see _run_with_model_fallback). Checked
    before each backoff sleep — a chain of 4 attempts x up to chat_timeout/3 retry_after
    can otherwise burn far longer than settings.chat_timeout while holding chat_lock
    (measured 2026-08-26: ~5800s of possible sleep against a 600s deadline).

    Retries with exponential backoff and equal jitter (~1s, ~2s, ~4s by default) up to
    max_attempts. When a provider reports a retry_after in its error body and it fits
    the cap (see _RETRY_AFTER_CAP_FRACTION), that wait wins over the exponential window.
    Non-transient errors propagate on the first failure so the caller's existing
    handlers see them.

    Sizing rationale — the upstream free GLM tier serves ~3 concurrent requests and
    429s the rest (measured 2026-07-15), while 10/10 sequential calls succeed. So a
    429 clears as soon as an in-flight request drains, and retrying is the only
    resilience available: there is no second provider to fall over to.
      - 4 attempts (was 3): worst-case exponential-only wait ~7s, well inside
        settings.chat_timeout (a configurable value, not the 120s repo
        default — see _RETRY_AFTER_CAP_FRACTION), and it lets a request
        survive three separate contention windows. A provider-reported
        retry_after can push a single wait up to chat_timeout / 3.
      - Equal jitter: several requests rejected by the same burst would otherwise
        wake in lockstep at exactly 1s and collide again. Each delay is randomised
        over [0.5×, 1.0×] of its window — half the backoff is fixed (guaranteeing
        the upstream gets some breathing room) and half is random (spreading the
        retries out). This is AWS's "equal jitter", NOT "full jitter", which would
        randomise over the whole [0, 1.0×] window and can retry almost instantly.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            if not _is_transient_llm_error(exc) or attempt == max_attempts:
                raise
            if deadline is not None and time.monotonic() >= deadline:
                raise
            exp_delay = base_delay * (2 ** (attempt - 1)) * random.uniform(0.5, 1.0)
            server_delay = _extract_retry_after_seconds(exc)
            # Honor the provider's requested wait when it's small enough to still
            # leave room for the retry itself inside settings.chat_timeout. llm7's
            # retry_after ranged 39s-84297s across one session (measured
            # 2026-08-23) — most of that range is a multi-hour throttle a single
            # HTTP request cannot wait out, so the cap covers only the short end
            # and falls back to the existing exponential backoff otherwise.
            retry_after_cap = settings.chat_timeout * _RETRY_AFTER_CAP_FRACTION
            if server_delay is not None and server_delay <= retry_after_cap:
                delay = max(server_delay, exp_delay)
            else:
                delay = exp_delay
            logger.warning(
                "Transient LLM error (attempt {}/{}): {} — retrying in {}s",
                attempt, max_attempts, str(exc)[:200], delay,
            )
            last_exc = exc
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc


def _api_model_id(model_id: str) -> str:
    """Strip a provider prefix for the API call (mirrors routes/agents.py:161-163).

    e.g. "cerebras/llama-3.3-70b" -> "llama-3.3-70b"; OpenRouter ":free" models
    keep their full id, since the slash there is part of the model name, not a
    provider prefix.
    """
    if "/" in model_id and not model_id.endswith(":free"):
        return model_id.split("/", 1)[1]
    return model_id


def _build_fallback_models(session) -> list[tuple[str, object | None]]:
    """Return [(label, model)] to try in order on a transient LLM failure.

    First entry is (session.model, None) — None tells pydantic-ai to use the
    agent's own configured model, i.e. a plain retry of what just failed.
    Remaining entries are LLM_FALLBACK_CHAIN models built as OpenAIModel bound
    to the session's own provider (same base_url/api_key the agent started
    with). A chain entry equal to session.model is skipped — retrying the
    model that already failed under a different label wastes an attempt.
    """
    models: list[tuple[str, object | None]] = [(session.model, None)]
    provider = getattr(session, "openai_provider", None)
    if provider is None:
        return models
    for model_id in _load_model_chain():
        if model_id == session.model:
            continue
        models.append((model_id, OpenAIModel(_api_model_id(model_id), provider=provider)))
    return models


_UNSET = object()  # distinguishes "pinned_model not passed" from the legitimate value None


def _remaining_timeout(deadline: float) -> float:
    """Return the seconds left before deadline, floored at 1.0.

    Used to shrink each retry/fallback attempt's model_settings timeout so the
    whole chain fits inside settings.chat_timeout instead of each attempt
    getting a fresh full-length timeout (see _run_with_model_fallback docstring
    for the arithmetic this prevents).
    """
    return max(1.0, deadline - time.monotonic())


async def _run_with_model_fallback(session, run_factory, *, deadline: float, pinned_model=_UNSET):
    """Run run_factory(model, timeout) across the session's fallback chain until success.

    run_factory: callable taking a model object (or None for "use agent default")
    and the remaining seconds until deadline, returning a coroutine — same
    reusable-factory contract as _run_with_llm_retry. Each model gets its own
    _run_with_llm_retry cycle, itself bounded by deadline; non-first models get
    a single retry attempt (no backoff-multiplied re-tries of a model that
    isn't even the agent's own) so the whole chain fits inside one
    settings.chat_timeout window instead of max_attempts-per-model x
    retry_after-per-attempt (measured 2026-08-26: unbounded, that arithmetic
    reaches ~5800s against a 600s chat_timeout while holding session.chat_lock).
    Non-transient errors (e.g. 401 config errors) propagate immediately without
    trying the next model — a bad key fails the same way on every model.

    pinned_model: when given (a sentinel distinguishes "not passed" from the
    legitimate value None = agent default), try only this model first instead
    of starting the chain over at the agent's own model — it already proved
    reachable this turn, so re-trying the model that just failed wastes an
    attempt. Falls through to the normal chain (skipping the pinned model,
    already tried) if the pinned model has failed again.

    Returns (result, model) — the winning model object (None means "agent
    default") so a caller running a follow-up turn (e.g. the deferred-tool
    approval loop) can pin it directly instead of re-walking the whole chain,
    including the model that just failed, on every iteration.
    """
    models = _build_fallback_models(session)
    if pinned_model is not _UNSET:
        pinned_name = None if pinned_model is None else pinned_model.model_name
        models = [("pinned", pinned_model)] + [
            (label, model) for label, model in models
            if (None if model is None else model.model_name) != pinned_name
        ]
    last_exc: Exception | None = None
    for label, model in models:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            attempts = 4 if model is None else 2
            result = await _run_with_llm_retry(
                lambda m=model: run_factory(m, _remaining_timeout(deadline)),
                max_attempts=attempts,
                deadline=deadline,
            )
            return result, model
        except Exception as exc:
            if not _is_transient_llm_error(exc):
                raise
            last_exc = exc
            logger.warning(
                "Model '{}' exhausted after transient errors, falling over — last: {}",
                label, str(exc)[:200],
            )
    if last_exc:
        raise last_exc
    raise RuntimeError("LLM fallback chain exhausted with no attempts made")


async def _run_chat_nonstream(
    hosted_id: str,
    body: ChatRequest,
    session,
    message_history_ref: list,
) -> ChatResponse:
    """Core non-streaming chat logic. Mutates message_history_ref in place.

    Separated to allow both legacy (global lock) and per-session (worker) paths
    to call the same implementation.
    """
    async with use_agent_context(
        agent_id=hosted_id,
        agent_handle=getattr(session, "agent_handle", None) or None,
        model=getattr(session, "model", None) or None,
    ):
        deadline = time.monotonic() + settings.chat_timeout
        try:
            result, winning_model = await _run_with_model_fallback(
                session,
                lambda model, timeout: session.agent.run(
                    body.content,
                    deps=session.deps,
                    message_history=message_history_ref,
                    model=model,
                    model_settings={"timeout": timeout},
                ),
                deadline=deadline,
            )
        except Exception as hist_err:
            if "unprocessed tool calls" in str(hist_err):
                logger.warning("Clearing corrupted history for {}: {}", hosted_id, hist_err)
                message_history_ref.clear()
                result, winning_model = await _run_with_model_fallback(
                    session,
                    lambda model, timeout: session.agent.run(
                        body.content,
                        deps=session.deps,
                        message_history=[],
                        model=model,
                        model_settings={"timeout": timeout},
                    ),
                    deadline=deadline,
                )
            elif _is_history_shape_error(hist_err):
                logger.warning(
                    "History shape rejected by model for {} ({}): clearing and retrying",
                    hosted_id, str(hist_err)[:120],
                )
                message_history_ref.clear()
                result, winning_model = await _run_with_model_fallback(
                    session,
                    lambda model, timeout: session.agent.run(
                        body.content,
                        deps=session.deps,
                        message_history=[],
                        model=model,
                        model_settings={"timeout": timeout},
                    ),
                    deadline=deadline,
                )
            else:
                raise

        new_history = sanitize_history(result.all_messages())[-100:]
        message_history_ref.clear()
        message_history_ref.extend(new_history)

        # Auto-approve deferred tool calls (execute requires approval in interrupt_on mode).
        # Non-streaming path must handle this loop itself — agent.run() stops at each
        # interrupt and must be resumed with DeferredToolResults.
        max_approvals = 10
        while isinstance(result.output, DeferredToolRequests) and max_approvals > 0:
            deferred = result.output
            approvals: dict[str, bool] = {}
            for tc in deferred.approvals:
                if tc.tool_name == "execute":
                    cmd = tc.args.get("command", "") if isinstance(tc.args, dict) else str(tc.args)
                    safe, reason = is_command_safe(cmd)
                    if not safe:
                        logger.warning("Blocked unsafe command from agent: {} ({})", cmd, reason)
                        approvals[tc.tool_call_id] = False
                        continue
                approvals[tc.tool_call_id] = True
            logger.info("Non-stream: auto-approving {} deferred tools", sum(v for v in approvals.values()))
            prev_messages = result.all_messages()
            # Pin the model that just won the fallback chain rather than re-walking
            # it from the agent's own (already-failed) model on every approval loop
            # iteration — the winning model just proved it's reachable.
            result, winning_model = await _run_with_model_fallback(
                session,
                lambda model, timeout: session.agent.run(
                    deferred_tool_results=DeferredToolResults(approvals=approvals),
                    deps=session.deps,
                    message_history=prev_messages,
                    model=model,
                    model_settings={"timeout": timeout},
                ),
                deadline=deadline,
                pinned_model=winning_model,
            )
            new_history = sanitize_history(result.all_messages())[-100:]
            message_history_ref.clear()
            message_history_ref.extend(new_history)
            max_approvals -= 1

        reply, tool_calls, thinking = _extract_response(result)
        return ChatResponse(reply=reply, tool_calls=tool_calls, thinking=thinking)


def _use_worker_pool(session) -> bool:
    """Return True when per-session concurrency is enabled (max_concurrent > 1)."""
    return session.worker_pool.max_concurrent > 1


@router.post("/agents/{hosted_id}/chat", response_model=ChatResponse)
async def chat_with_agent(hosted_id: str, body: ChatRequest):
    """Send a message to the hosted agent and get a reply (non-streaming fallback).

    Phase 2+3: when owner_session_id is provided and max_concurrent_sessions > 1,
    uses per-session WorkerPool isolation. Falls back to legacy global lock for
    single-session agents (backward compat).
    """
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(400, "Agent not running. Start it first.")

    session.touch()

    # ── Per-session path (Phase 2+3) ────────────────────────────────────────
    if body.owner_session_id and _use_worker_pool(session):
        worker: SessionWorker | None = None
        try:
            worker = await session.worker_pool.acquire_slot(body.owner_session_id)
        except Exception as e:
            raise HTTPException(503, f"Worker pool unavailable: {e}")

        try:
            # Within-session serialization
            try:
                await asyncio.wait_for(worker.lock.acquire(), timeout=settings.chat_queue_timeout)
            except asyncio.TimeoutError:
                session.worker_pool.release_slot(worker)
                raise HTTPException(429, "Session busy — try again later")

            worker.touch()
            # Reflect in global active_session_id for /status compat
            session.active_session_id = body.owner_session_id

            try:
                _chat_started_at = time.monotonic()
                async with session.worker_pool.llm_semaphore:
                    result_resp = await _run_chat_nonstream(
                        hosted_id, body, session, worker.message_history
                    )
                maybe_sample(
                    hosted_agent_id=hosted_id,
                    agent_handle=getattr(session, "agent_handle", None) or "",
                    model=getattr(session, "model", None) or "",
                    trace_id=None,
                    input_messages=[{"role": "user", "content": body.content}],
                    output_text=result_resp.reply,
                    tool_calls=result_resp.tool_calls or [],
                    started_at=_chat_started_at,
                    status="completed",
                )
                return result_resp
            except Exception as e:
                logger.error("Chat error for {} session {}: {}", hosted_id, body.owner_session_id, repr(e))
                raise HTTPException(500, f"Agent error: {str(e)}")
            finally:
                session.active_session_id = None
                session.bootstrap_done = True
                worker.lock.release()
        finally:
            if worker is not None:
                session.worker_pool.release_slot(worker)
        return  # unreachable, satisfies type checker  # type: ignore[return-value]

    # ── Legacy global-lock path (default, single-session compat) ────────────
    try:
        await asyncio.wait_for(session.chat_lock.acquire(), timeout=settings.chat_queue_timeout)
    except asyncio.TimeoutError:
        raise HTTPException(429, "Agent busy — try again later")

    # Track which session owns the lock so /status can report busy_session_id.
    session.active_session_id = body.owner_session_id

    _chat_started_at = time.monotonic()
    try:
        result_resp = await _run_chat_nonstream(hosted_id, body, session, session.message_history)
        maybe_sample(
            hosted_agent_id=hosted_id,
            agent_handle=getattr(session, "agent_handle", None) or "",
            model=getattr(session, "model", None) or "",
            trace_id=None,
            input_messages=[{"role": "user", "content": body.content}],
            output_text=result_resp.reply,
            tool_calls=result_resp.tool_calls or [],
            started_at=_chat_started_at,
            status="completed",
        )
        return result_resp
    except Exception as e:
        logger.error("Chat error for {}: {}", hosted_id, repr(e))
        raise HTTPException(500, f"Agent error: {str(e)}")
    finally:
        session.active_session_id = None
        session.bootstrap_done = True
        session.chat_lock.release()


@router.post("/agents/{hosted_id}/chat/stream")
async def chat_stream(hosted_id: str, body: ChatRequest):
    """Stream chat response as ndjson events.

    Events:
      {"type": "text_delta", "content": "..."}     — incremental text
      {"type": "tool_call", "tool_name": "...", "args": ...}  — tool invocation
      {"type": "tool_result", "tool_name": "...", "output": "..."} — tool output
      {"type": "thinking_delta", "content": "..."}  — thinking text
      {"type": "done", "reply": "...", "tool_calls": [...], "thinking": "..."} — final
      {"type": "error", "message": "..."}           — error

    Phase 2+3: per-session isolation. When owner_session_id is set and
    max_concurrent_sessions > 1, uses WorkerPool SessionWorker's history and lock.
    Falls back to legacy global chat_lock for single-session agents.
    """
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(400, "Agent not running. Start it first.")

    session.touch()

    # ── Determine which lock + history to use ───────────────────────────────
    worker: SessionWorker | None = None
    use_pool = body.owner_session_id and _use_worker_pool(session)

    if use_pool:
        # Acquire executor slot (cross-session concurrency)
        try:
            worker = await session.worker_pool.acquire_slot(body.owner_session_id)  # type: ignore[arg-type]
        except Exception as e:
            raise HTTPException(503, f"Worker pool unavailable: {e}")
        # Acquire per-session lock OUTSIDE generate() for GC-safe release
        try:
            await asyncio.wait_for(worker.lock.acquire(), timeout=settings.chat_queue_timeout)
        except asyncio.TimeoutError:
            session.worker_pool.release_slot(worker)
            raise HTTPException(429, "Session busy — try again later")
        worker.touch()
        _lock_to_release = worker.lock
        _history = worker.message_history
    else:
        # Legacy: acquire global lock OUTSIDE the StreamingResponse generator so release
        # is guaranteed in finally — `async with` inside a generator may not run
        # __aexit__ if the generator is GC'd in a different async context after
        # a `RuntimeError: async generator raised StopAsyncIteration` (pydantic-ai
        # bug #4204; partial fix in 1.77.0 covers _stream_text_deltas but not
        # the agent.iter() node.stream() path we use).
        try:
            await asyncio.wait_for(session.chat_lock.acquire(), timeout=settings.chat_queue_timeout)
        except asyncio.TimeoutError:
            raise HTTPException(429, "Agent busy — try again later")
        _lock_to_release = session.chat_lock
        _history = session.message_history

    # Track which session owns the lock so /status can report busy_session_id.
    session.active_session_id = body.owner_session_id

    # _history and _lock_to_release are bound by the per-session / global-lock
    # selection above. The generate() closure captures them by name.

    async def generate():
        _stream_started_at = time.monotonic()
        _deadline = time.monotonic() + settings.chat_timeout
        # Set on the FIRST yield of any event to the client. Once true, a
        # transient-error fallover (see the `elif _is_transient_llm_error(e):`
        # branch below) must NOT re-run the request on another model — the
        # client already has a partial reply and would receive it twice
        # (a streamed "text_delta" fragment, then a full "done" reply from the
        # fallback model). INVARIANT(runner-llm7-markers): do not remove this
        # gate without also removing the fallover it guards.
        _yielded_to_client = False
        _fallback_models = iter(_build_fallback_models(session))
        _fb_label, _fb_model = next(_fallback_models)
        try:
            async with use_agent_context(
                agent_id=hosted_id,
                agent_handle=getattr(session, "agent_handle", None) or None,
                model=getattr(session, "model", None) or None,
            ):
                try:
                    # Try streaming via agent.iter()
                    try:
                        iter_ctx = session.agent.iter(
                            body.content,
                            deps=session.deps,
                            message_history=_history,
                            model=_fb_model,
                            model_settings={"timeout": _remaining_timeout(_deadline)},
                        )
                    except Exception as hist_err:
                        if "unprocessed tool calls" in str(hist_err):
                            logger.warning("Clearing corrupted history: {}", hist_err)
                            _history.clear()
                            iter_ctx = session.agent.iter(
                                body.content,
                                deps=session.deps,
                                message_history=[],
                                model=_fb_model,
                                model_settings={"timeout": _remaining_timeout(_deadline)},
                            )
                        else:
                            raise
                    all_tool_calls: list[dict] = []

                    async with iter_ctx as run:
                        async for node in run:
                            # A node was reached, meaning agent.iter() produced at
                            # least one step of the run. Every code path below this
                            # point can yield an event to the client (text_delta,
                            # tool_call, tool_result, ...), so a transient error
                            # raised from here on must not trigger the model-fallover
                            # retry (see _yielded_to_client comment above generate()).
                            _yielded_to_client = True
                            node_name = type(node).__name__

                            # Stream text deltas from model request nodes
                            if hasattr(node, 'stream') and 'Request' in node_name:
                                tool_names_by_id: dict[str, str] = {}
                                try:
                                    async with node.stream(run.ctx) as stream:
                                        async for event in stream:
                                            # PartStartEvent carries the INITIAL snapshot of a new
                                            # text/thinking part — first chunk was being dropped
                                            # because only PartDeltaEvent was handled below.
                                            if isinstance(event, PartStartEvent):
                                                part = getattr(event, 'part', None)
                                                if isinstance(part, TextPart) and part.content:
                                                    yield json.dumps({"type": "text_delta", "content": part.content}) + "\n"
                                                elif isinstance(part, ThinkingPart) and part.content:
                                                    yield json.dumps({"type": "thinking_delta", "content": part.content}) + "\n"
                                                elif isinstance(part, ToolCallPart):
                                                    tool_names_by_id[part.tool_call_id] = part.tool_name
                                                continue
                                            if hasattr(event, 'delta'):
                                                delta = event.delta
                                                cd = getattr(delta, 'content_delta', None)
                                                if cd:
                                                    kind = getattr(delta, 'part_delta_kind', 'text')
                                                    if kind == 'thinking':
                                                        yield json.dumps({"type": "thinking_delta", "content": cd}) + "\n"
                                                    else:
                                                        yield json.dumps({"type": "text_delta", "content": cd}) + "\n"
                                            # Capture tool result events with output preview
                                            elif isinstance(event, FunctionToolResultEvent):
                                                tool_name = tool_names_by_id.get(event.tool_call_id, "unknown")
                                                output = str(event.result.content)[:2000] if event.result else ""
                                                yield json.dumps({
                                                    "type": "tool_result",
                                                    "tool_name": tool_name,
                                                    "output": output,
                                                }) + "\n"
                                                # Stream todos update when todo tools are called
                                                if tool_name in ("write_todos", "add_todo", "update_todo_status", "remove_todo"):
                                                    todos_file = settings.workspace_root / hosted_id / "todos.json"
                                                    if todos_file.exists():
                                                        try:
                                                            todos_data = json.loads(todos_file.read_text())
                                                            yield json.dumps({"type": "todos_update", "todos": todos_data}) + "\n"
                                                        except Exception:
                                                            pass
                                            # Track tool call IDs for result mapping
                                            elif hasattr(event, 'part') and isinstance(getattr(event, 'part', None), ToolCallPart):
                                                tc_part = event.part
                                                tool_names_by_id[tc_part.tool_call_id] = tc_part.tool_name
                                except Exception as e:
                                    logger.debug("Node stream not available: {}", e)

                            # Report tool calls from model response
                            if hasattr(node, 'model_response') and hasattr(node.model_response, 'parts'):
                                for part in node.model_response.parts:
                                    if isinstance(part, ToolCallPart):
                                        args = part.args if isinstance(part.args, dict) else str(part.args)
                                        yield json.dumps({
                                            "type": "tool_call",
                                            "tool_name": part.tool_name,
                                            "args": args,
                                        }) + "\n"
                                        all_tool_calls.append({
                                            "tool": part.tool_name,
                                            "args": args,
                                            "status": "done",
                                            "tool_call_id": part.tool_call_id,
                                        })

                        result = run.result
                        new_hist = sanitize_history(result.all_messages())[-100:]
                        _history.clear()
                        _history.extend(new_hist)

                        # Auto-approve deferred tool calls (agent runs in sandbox)
                        max_approvals = 10
                        while isinstance(result.output, DeferredToolRequests) and max_approvals > 0:
                            deferred = result.output
                            approvals: dict[str, bool] = {}
                            for tc in deferred.approvals:
                                args = tc.args if isinstance(tc.args, dict) else str(tc.args)
                                # Filter dangerous commands
                                if tc.tool_name == "execute":
                                    cmd = tc.args.get("command", "") if isinstance(tc.args, dict) else str(tc.args)
                                    safe, reason = is_command_safe(cmd)
                                    if not safe:
                                        logger.warning("Blocked unsafe command from agent: {} ({})", cmd, reason)
                                        approvals[tc.tool_call_id] = False
                                        yield json.dumps({"type": "tool_call", "tool_name": tc.tool_name, "args": f"BLOCKED: {reason}"}) + "\n"
                                        continue
                                approvals[tc.tool_call_id] = True
                                yield json.dumps({"type": "tool_call", "tool_name": tc.tool_name, "args": args}) + "\n"
                                all_tool_calls.append({
                                    "tool": tc.tool_name,
                                    "args": args,
                                    "status": "done",
                                    "tool_call_id": tc.tool_call_id,
                                })
                            logger.info("Auto-approving {} deferred tools ({} blocked)", sum(v for v in approvals.values()), sum(1 for v in approvals.values() if not v))
                            result = await session.agent.run(
                                deferred_tool_results=DeferredToolResults(approvals=approvals),
                                deps=session.deps,
                                message_history=result.all_messages(),
                                model=_fb_model,
                                model_settings={"timeout": _remaining_timeout(_deadline)},
                            )
                            new_hist = sanitize_history(result.all_messages())[-100:]
                            _history.clear()
                            _history.extend(new_hist)
                            # Backfill tool results from all_messages into all_tool_calls.
                            # new_messages() on a deferred run only contains ToolReturnPart + final
                            # text — no ToolCallPart — so _extract_response would yield extra_tools=[].
                            # Match by tool_call_id (unique per call) so that multiple calls of the
                            # same tool in one turn (e.g. several execute() invocations) each receive
                            # their own result instead of all sharing the first/last one.
                            results_by_id: dict[str, str] = {}
                            for msg in result.all_messages():
                                if not hasattr(msg, "parts"):
                                    continue
                                for part in msg.parts:
                                    if isinstance(part, ToolReturnPart):
                                        results_by_id[part.tool_call_id] = str(part.content)[:500]
                            for tc in all_tool_calls:
                                tcid = tc.get("tool_call_id")
                                if tcid and tcid in results_by_id and "result" not in tc:
                                    tc["result"] = results_by_id[tcid]
                            # Emit tool_result events with actual output now that results are available.
                            for tc in deferred.approvals:
                                if approvals.get(tc.tool_call_id):
                                    output = results_by_id.get(tc.tool_call_id, "")
                                    yield json.dumps({"type": "tool_result", "tool_name": tc.tool_name, "output": output}) + "\n"
                            max_approvals -= 1

                        reply, extra_tools, thinking = _extract_response(result)
                        # Merge: extra_tools first (has results), then streaming ones
                        seen = set()
                        final_tools = []
                        for tc in (extra_tools + all_tool_calls):
                            key = (tc.get("tool"), str(tc.get("args")))
                            if key not in seen:
                                seen.add(key)
                                final_tools.append(tc)
                        # Emit todos update from read_todos result if available
                        for tc in final_tools:
                            if tc.get("tool") == "read_todos" and tc.get("result"):
                                # Parse todo items from read_todos result text
                                todos_items = []
                                for line in str(tc["result"]).split("\n"):
                                    line = line.strip()
                                    if line.startswith("1.") or line.startswith("2.") or line.startswith("3.") or line.startswith("4.") or line.startswith("5."):
                                        is_done = "[x]" in line or "[X]" in line
                                        is_progress = "◉" in line or "[~]" in line
                                        content = line.split("]", 1)[-1].strip() if "]" in line else line[3:].strip()
                                        todos_items.append({
                                            "content": content,
                                            "status": "completed" if is_done else "in_progress" if is_progress else "pending",
                                        })
                                if todos_items:
                                    yield json.dumps({"type": "todos_update", "todos": todos_items}) + "\n"
                                break

                        yield json.dumps({
                            "type": "done",
                            "reply": reply,
                            "tool_calls": final_tools,
                            "thinking": thinking,
                        }) + "\n"
                        maybe_sample(
                            hosted_agent_id=hosted_id,
                            agent_handle=getattr(session, "agent_handle", None) or "",
                            model=getattr(session, "model", None) or "",
                            trace_id=None,
                            input_messages=[{"role": "user", "content": body.content}],
                            output_text=reply,
                            tool_calls=final_tools,
                            started_at=_stream_started_at,
                            status="completed",
                        )

                except AttributeError:
                    # agent.iter() not available — use non-streaming agent.run()
                    logger.info("Streaming not available, falling back to agent.run()")
                    try:
                        try:
                            result = await session.agent.run(
                                body.content,
                                deps=session.deps,
                                message_history=_history,
                                model=_fb_model,
                                model_settings={"timeout": _remaining_timeout(_deadline)},
                            )
                        except Exception as hist_err2:
                            if "unprocessed tool calls" in str(hist_err2):
                                logger.warning("Fallback: clearing corrupted history: {}", hist_err2)
                                _history.clear()
                                result = await session.agent.run(
                                    body.content,
                                    deps=session.deps,
                                    message_history=[],
                                    model=_fb_model,
                                    model_settings={"timeout": _remaining_timeout(_deadline)},
                                )
                            else:
                                raise
                        new_hist = sanitize_history(result.all_messages())[-100:]
                        _history.clear()
                        _history.extend(new_hist)

                        # Auto-approve deferred tool calls
                        max_approvals = 10
                        while isinstance(result.output, DeferredToolRequests) and max_approvals > 0:
                            deferred = result.output
                            approvals: dict[str, bool] = {}
                            for tc in deferred.approvals:
                                if tc.tool_name == "execute":
                                    cmd = tc.args.get("command", "") if isinstance(tc.args, dict) else str(tc.args)
                                    safe, reason = is_command_safe(cmd)
                                    if not safe:
                                        logger.warning("Blocked unsafe command (fallback): {} ({})", cmd, reason)
                                        approvals[tc.tool_call_id] = False
                                        continue
                                approvals[tc.tool_call_id] = True
                            logger.info("Auto-approving {} deferred tools (fallback)", len(approvals))
                            result = await session.agent.run(
                                deferred_tool_results=DeferredToolResults(approvals=approvals),
                                deps=session.deps,
                                message_history=result.all_messages(),
                                model=_fb_model,
                                model_settings={"timeout": _remaining_timeout(_deadline)},
                            )
                            new_hist = sanitize_history(result.all_messages())[-100:]
                            _history.clear()
                            _history.extend(new_hist)
                            max_approvals -= 1

                        reply, tool_calls, thinking = _extract_response(result)
                        yield json.dumps({
                            "type": "done",
                            "reply": reply,
                            "tool_calls": tool_calls,
                            "thinking": thinking,
                        }) + "\n"
                        maybe_sample(
                            hosted_agent_id=hosted_id,
                            agent_handle=getattr(session, "agent_handle", None) or "",
                            model=getattr(session, "model", None) or "",
                            trace_id=None,
                            input_messages=[{"role": "user", "content": body.content}],
                            output_text=reply,
                            tool_calls=tool_calls,
                            started_at=_stream_started_at,
                            status="completed",
                        )
                    except Exception as e2:
                        logger.error("Fallback chat error: {}", repr(e2))
                        yield json.dumps({"type": "error", "message": str(e2)}) + "\n"

                except Exception as e:
                    _needs_history_clear = (
                        "unprocessed tool calls" in str(e)
                        or _is_history_shape_error(e)
                    )
                    if _needs_history_clear:
                        logger.warning("Stream: clearing history and retrying for {}: {}", hosted_id, str(e)[:120])
                        _history.clear()
                        try:
                            result = await session.agent.run(
                                body.content,
                                deps=session.deps,
                                message_history=[],
                                model=_fb_model,
                                model_settings={"timeout": _remaining_timeout(_deadline)},
                            )
                            new_hist = sanitize_history(result.all_messages())[-100:]
                            _history.clear()
                            _history.extend(new_hist)
                            reply, tool_calls, thinking = _extract_response(result)
                            yield json.dumps({
                                "type": "done",
                                "reply": reply,
                                "tool_calls": tool_calls,
                                "thinking": thinking,
                            }) + "\n"
                            maybe_sample(
                                hosted_agent_id=hosted_id,
                                agent_handle=getattr(session, "agent_handle", None) or "",
                                model=getattr(session, "model", None) or "",
                                trace_id=None,
                                input_messages=[{"role": "user", "content": body.content}],
                                output_text=reply,
                                tool_calls=tool_calls,
                                started_at=_stream_started_at,
                                status="completed",
                            )
                        except Exception as e2:
                            logger.error("Retry after history clear failed: {}", repr(e2))
                            yield json.dumps({"type": "error", "message": str(e2)}) + "\n"
                    elif _is_transient_llm_error(e) and not _yielded_to_client and time.monotonic() < _deadline:
                        # Safe to fall over to the next model and retry via
                        # non-streaming agent.run() ONLY when nothing reached the
                        # client yet (see _yielded_to_client) — otherwise the
                        # fallover would emit a full "done" reply on top of the
                        # partial text the client already received.
                        next_model = next(_fallback_models, None)
                        if next_model is None:
                            logger.error("Stream error for {}: {} (fallback chain exhausted)", hosted_id, repr(e))
                            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
                        else:
                            _fb_label, _fb_model = next_model
                            logger.warning(
                                "Stream: model failed with transient error, falling over to '{}': {}",
                                _fb_label, str(e)[:200],
                            )
                            try:
                                result = await session.agent.run(
                                    body.content,
                                    deps=session.deps,
                                    message_history=_history,
                                    model=_fb_model,
                                    model_settings={"timeout": _remaining_timeout(_deadline)},
                                )
                                new_hist = sanitize_history(result.all_messages())[-100:]
                                _history.clear()
                                _history.extend(new_hist)
                                reply, tool_calls, thinking = _extract_response(result)
                                yield json.dumps({
                                    "type": "done",
                                    "reply": reply,
                                    "tool_calls": tool_calls,
                                    "thinking": thinking,
                                }) + "\n"
                                maybe_sample(
                                    hosted_agent_id=hosted_id,
                                    agent_handle=getattr(session, "agent_handle", None) or "",
                                    model=getattr(session, "model", None) or "",
                                    trace_id=None,
                                    input_messages=[{"role": "user", "content": body.content}],
                                    output_text=reply,
                                    tool_calls=tool_calls,
                                    started_at=_stream_started_at,
                                    status="completed",
                                )
                            except Exception as e2:
                                logger.error("Stream fallback to '{}' failed: {}", _fb_label, repr(e2))
                                yield json.dumps({"type": "error", "message": str(e2)}) + "\n"
                    else:
                        logger.error("Stream error for {}: {}", hosted_id, repr(e))
                        yield json.dumps({"type": "error", "message": str(e)}) + "\n"
        finally:
            # Always release lock + pool slot, even if the generator is abandoned
            # mid-stream (client disconnect, RuntimeError from upstream pydantic-ai).
            session.active_session_id = None
            session.bootstrap_done = True
            if _lock_to_release.locked():
                _lock_to_release.release()
            # Release executor slot if we used the worker pool
            if worker is not None:
                session.worker_pool.release_slot(worker)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
