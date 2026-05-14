"""Chat endpoints: chat (non-streaming) and chat/stream."""

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
from sandbox import is_command_safe
from schemas import ChatRequest, ChatResponse
from session import sanitize_history, sessions

settings = get_settings()

router = APIRouter()


@router.post("/agents/{hosted_id}/chat", response_model=ChatResponse)
async def chat_with_agent(hosted_id: str, body: ChatRequest):
    """Send a message to the hosted agent and get a reply (non-streaming fallback)."""
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(400, "Agent not running. Start it first.")

    session.touch()

    try:
        await asyncio.wait_for(session.chat_lock.acquire(), timeout=settings.chat_queue_timeout)
    except asyncio.TimeoutError:
        raise HTTPException(429, "Agent busy — try again later")

    try:
        try:
            result = await session.agent.run(
                body.content,
                deps=session.deps,
                message_history=session.message_history,
                model_settings={"timeout": settings.chat_timeout},
            )
        except Exception as hist_err:
            if "unprocessed tool calls" in str(hist_err):
                logger.warning("Clearing corrupted history for {}: {}", hosted_id, hist_err)
                session.message_history = []
                result = await session.agent.run(
                    body.content,
                    deps=session.deps,
                    message_history=[],
                    model_settings={"timeout": settings.chat_timeout},
                )
            else:
                raise
        session.message_history = sanitize_history(result.all_messages())[-100:]

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
            result = await session.agent.run(
                deferred_tool_results=DeferredToolResults(approvals=approvals),
                deps=session.deps,
                message_history=result.all_messages(),
                model_settings={"timeout": settings.chat_timeout},
            )
            session.message_history = sanitize_history(result.all_messages())[-100:]
            max_approvals -= 1

        reply, tool_calls, thinking = _extract_response(result)
        return ChatResponse(reply=reply, tool_calls=tool_calls, thinking=thinking)
    except Exception as e:
        logger.error("Chat error for {}: {}", hosted_id, repr(e))
        raise HTTPException(500, f"Agent error: {str(e)}")
    finally:
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
    """
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(400, "Agent not running. Start it first.")

    session.touch()

    # Acquire chat lock OUTSIDE the StreamingResponse generator so release
    # is guaranteed in finally — `async with` inside a generator may not run
    # __aexit__ if the generator is GC'd in a different async context after
    # a `RuntimeError: async generator raised StopAsyncIteration` (pydantic-ai
    # bug #4204; partial fix in 1.77.0 covers _stream_text_deltas but not
    # the agent.iter() node.stream() path we use).
    try:
        await asyncio.wait_for(session.chat_lock.acquire(), timeout=settings.chat_queue_timeout)
    except asyncio.TimeoutError:
        raise HTTPException(429, "Agent busy — try again later")

    async def generate():
      try:
        try:
            # Try streaming via agent.iter()
            try:
                iter_ctx = session.agent.iter(
                    body.content,
                    deps=session.deps,
                    message_history=session.message_history,
                    model_settings={"timeout": settings.chat_timeout},
                )
            except Exception as hist_err:
                if "unprocessed tool calls" in str(hist_err):
                    logger.warning("Clearing corrupted history: {}", hist_err)
                    session.message_history = []
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
                session.message_history = sanitize_history(result.all_messages())[-100:]

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
                    session.message_history = sanitize_history(result.all_messages())[-100:]
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
                        message_history=session.message_history,
                        model_settings={"timeout": settings.chat_timeout},
                    )
                except Exception as hist_err2:
                    if "unprocessed tool calls" in str(hist_err2):
                        logger.warning("Fallback: clearing corrupted history: {}", hist_err2)
                        session.message_history = []
                        result = await session.agent.run(
                            body.content,
                            deps=session.deps,
                            message_history=[],
                            model_settings={"timeout": settings.chat_timeout},
                        )
                    else:
                        raise
                session.message_history = sanitize_history(result.all_messages())[-100:]

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
                    session.message_history = sanitize_history(result.all_messages())[-100:]
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
            if "unprocessed tool calls" in str(e):
                logger.warning("Stream: clearing corrupted history and retrying: {}", e)
                session.message_history = []
                try:
                    result = await session.agent.run(
                        body.content,
                        deps=session.deps,
                        message_history=[],
                        model_settings={"timeout": settings.chat_timeout},
                    )
                    session.message_history = sanitize_history(result.all_messages())[-100:]
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
        # Always release lock, even if the generator is abandoned mid-stream
        # (client disconnect, RuntimeError from upstream pydantic-ai).
        if session.chat_lock.locked():
            session.chat_lock.release()

    return StreamingResponse(generate(), media_type="application/x-ndjson")
