"""Shared helper functions for route handlers."""

from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import TextPart, ThinkingPart, ToolCallPart, ToolReturnPart


def _extract_response(result) -> tuple[str, list[dict], str | None]:
    """Extract reply text, tool_calls, and thinking from agent run result."""
    tool_calls: list[dict] = []
    thinking_parts: list[str] = []
    reply_parts: list[str] = []

    for msg in result.new_messages():
        if not hasattr(msg, 'parts'):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                args = part.args if isinstance(part.args, dict) else str(part.args)
                tool_calls.append({"tool": part.tool_name, "args": args, "status": "done"})
            elif isinstance(part, ToolReturnPart):
                result_text = str(part.content)[:500]
                for tc in reversed(tool_calls):
                    if tc.get("tool") == part.tool_name and "result" not in tc:
                        tc["result"] = result_text
                        break
            elif isinstance(part, ThinkingPart) and part.content:
                thinking_parts.append(part.content)
            elif isinstance(part, TextPart) and part.content:
                reply_parts.append(part.content)

    if reply_parts:
        reply = reply_parts[-1]
    elif isinstance(result.output, DeferredToolRequests):
        reply = "Done."
    else:
        reply = str(result.output) if result.output else "Done."
    thinking = "\n".join(thinking_parts) or None
    return reply, tool_calls, thinking
