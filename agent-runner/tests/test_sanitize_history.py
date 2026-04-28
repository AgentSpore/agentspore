"""Smoke tests for sanitize_history — orphan ToolCallPart handling.

Regression coverage for the "Tool call was cancelled." bug where an
aborted stream left an orphan ToolCallPart in saved history. On next
restore, pydantic-deep would inject a synthetic cancelled return, and
the agent would hallucinate that sandbox/network was blocked.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from main import sanitize_history  # noqa: E402


def test_drops_trailing_orphan_tool_call():
    history = [
        ModelRequest(parts=[UserPromptPart(content="run curl")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="execute", args={"command": "curl x"}, tool_call_id="t1")]
        ),
    ]
    cleaned = sanitize_history(history)
    assert len(cleaned) == 1
    assert isinstance(cleaned[0], ModelRequest)


def test_keeps_paired_tool_call():
    history = [
        ModelRequest(parts=[UserPromptPart(content="run curl")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="execute", args={}, tool_call_id="t1")]
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="execute", content="ok", tool_call_id="t1")]
        ),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    cleaned = sanitize_history(history)
    assert len(cleaned) == 4


def test_keeps_text_only_response():
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content="hello")]),
    ]
    cleaned = sanitize_history(history)
    assert len(cleaned) == 2


def test_handles_serialized_dict_form():
    history = [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "x"}]},
        {
            "kind": "response",
            "parts": [{"part_kind": "tool-call", "tool_name": "execute", "tool_call_id": "t1", "args": {}}],
        },
    ]
    cleaned = sanitize_history(history)
    assert len(cleaned) == 1
    assert cleaned[0]["kind"] == "request"


def test_empty():
    assert sanitize_history([]) == []
