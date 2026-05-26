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

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic_ai import DeferredToolRequests, FunctionToolResultEvent
from pydantic_ai.messages import PartStartEvent, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import DeferredToolResults

from config import get_settings
from helpers import _extract_response
from observability import use_agent_context
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
    "502 Bad Gateway",
    "504 Gateway Timeout",
)

# Markers indicating the conversation history has an illegal shape for the
# current model provider.  These errors are not transient (retrying with the
# same history will always fail), so they are handled separately: history is
# cleared before the retry rather than retrying as-is.
_HISTORY_SHAPE_ERROR_MARKERS: tuple[str, ...] = (
    "messages parameter is illegal",
    "1214",
)


def _is_transient_llm_error(exc: Exception) -> bool:
    """Return True if an exception is a flaky upstream LLM response worth retrying."""
    msg = str(exc)
    return any(marker in msg for marker in _TRANSIENT_LLM_ERROR_MARKERS)


def _is_history_shape_error(exc: Exception) -> bool:
    """Return True if the error is caused by illegal conversation history shape.

    Z.AI error 1214 ("messages parameter is illegal") falls in this category.
    The fix is to clear / trim message_history before retrying, NOT to retry
    the same request unchanged.
    """
    msg = str(exc)
    return any(marker in msg for marker in _HISTORY_SHAPE_ERROR_MARKERS)


async def _run_with_llm_retry(coro_factory, *, max_attempts: int = 3, base_delay: float = 1.0):
    """Invoke an async agent.run() coroutine, retrying on transient upstream LLM errors.

    coro_factory: callable that returns a fresh coroutine (NOT a bare coroutine — those
    cannot be re-awaited after a failure). Pass `lambda: session.agent.run(...)`.

    Retries with exponential backoff (1s, 2s, 4s by default) up to max_attempts. Non-transient
    errors propagate on the first failure so the caller's existing handlers see them.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            if not _is_transient_llm_error(exc) or attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Transient LLM error (attempt {}/{}): {} — retrying in {}s",
                attempt, max_attempts, str(exc)[:200], delay,
            )
            last_exc = exc
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc


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
        try:
            result = await _run_with_llm_retry(lambda: session.agent.run(
                body.content,
                deps=session.deps,
                message_history=message_history_ref,
                model_settings={"timeout": settings.chat_timeout},
            ))
        except Exception as hist_err:
            if "unprocessed tool calls" in str(hist_err):
                logger.warning("Clearing corrupted history for {}: {}", hosted_id, hist_err)
                message_history_ref.clear()
                result = await _run_with_llm_retry(lambda: session.agent.run(
                    body.content,
                    deps=session.deps,
                    message_history=[],
                    model_settings={"timeout": settings.chat_timeout},
                ))
            elif _is_history_shape_error(hist_err):
                logger.warning(
                    "History shape rejected by model for {} ({}): clearing and retrying",
                    hosted_id, str(hist_err)[:120],
                )
                message_history_ref.clear()
                result = await _run_with_llm_retry(lambda: session.agent.run(
                    body.content,
                    deps=session.deps,
                    message_history=[],
                    model_settings={"timeout": settings.chat_timeout},
                ))
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
            result = await _run_with_llm_retry(lambda: session.agent.run(
                deferred_tool_results=DeferredToolResults(approvals=approvals),
                deps=session.deps,
                message_history=prev_messages,
                model_settings={"timeout": settings.chat_timeout},
            ))
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
                async with session.worker_pool.llm_semaphore:
                    result_resp = await _run_chat_nonstream(
                        hosted_id, body, session, worker.message_history
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

    try:
        result_resp = await _run_chat_nonstream(hosted_id, body, session, session.message_history)
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
                            model_settings={"timeout": settings.chat_timeout},
                        )
                    except Exception as hist_err:
                        if "unprocessed tool calls" in str(hist_err):
                            logger.warning("Clearing corrupted history: {}", hist_err)
                            _history.clear()
                            iter_ctx = session.agent.iter(
                                body.content,
                                deps=session.deps,
                                message_history=[],
                                model_settings={"timeout": settings.chat_timeout},
                            )
                        else:
                            raise
                    all_tool_calls: list[dict] = []

                    async with iter_ctx as run:
                        async for node in run:
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
                                model_settings={"timeout": settings.chat_timeout},
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

                except AttributeError:
                    # agent.iter() not available — use non-streaming agent.run()
                    logger.info("Streaming not available, falling back to agent.run()")
                    try:
                        try:
                            result = await session.agent.run(
                                body.content,
                                deps=session.deps,
                                message_history=_history,
                                model_settings={"timeout": settings.chat_timeout},
                            )
                        except Exception as hist_err2:
                            if "unprocessed tool calls" in str(hist_err2):
                                logger.warning("Fallback: clearing corrupted history: {}", hist_err2)
                                _history.clear()
                                result = await session.agent.run(
                                    body.content,
                                    deps=session.deps,
                                    message_history=[],
                                    model_settings={"timeout": settings.chat_timeout},
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
                                model_settings={"timeout": settings.chat_timeout},
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
                                model_settings={"timeout": settings.chat_timeout},
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
                        except Exception as e2:
                            logger.error("Retry after history clear failed: {}", repr(e2))
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
