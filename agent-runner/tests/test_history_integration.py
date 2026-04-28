"""Integration test for /agents/{id}/history endpoint sanitize roundtrip.

Builds a fake AgentSession with dirty history (orphan trailing
ToolCallPart), hits GET /history, asserts the serialized output is
sanitized (no orphan response) so the next restore won't trigger
"Tool call was cancelled." injection.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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

import main  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


def _fake_session(history):
    s = MagicMock()
    s.message_history = history
    return s


def test_history_endpoint_strips_orphan_trailing_tool_call(client):
    hosted_id = "test-orphan"
    history = [
        ModelRequest(parts=[UserPromptPart(content="run curl")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="execute", args={"command": "curl x"}, tool_call_id="t1")]
        ),
    ]
    main.sessions[hosted_id] = _fake_session(history)
    try:
        resp = client.get(f"/agents/{hosted_id}/history")
        assert resp.status_code == 200
        data = resp.json()["history"]
        assert len(data) == 1
        assert data[0]["kind"] == "request"
    finally:
        main.sessions.pop(hosted_id, None)


def test_history_endpoint_keeps_paired_tool_call(client):
    hosted_id = "test-paired"
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
    main.sessions[hosted_id] = _fake_session(history)
    try:
        resp = client.get(f"/agents/{hosted_id}/history")
        assert resp.status_code == 200
        data = resp.json()["history"]
        assert len(data) == 4
    finally:
        main.sessions.pop(hosted_id, None)


def test_history_endpoint_unknown_session(client):
    resp = client.get("/agents/does-not-exist/history")
    assert resp.status_code == 200
    assert resp.json() == {"history": []}


def test_restore_path_sanitize_via_dict_form():
    """Verify dict-form sanitize (legacy DB rows) drops orphan trailing."""
    from main import sanitize_history

    dict_history = [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "x"}]},
        {
            "kind": "response",
            "parts": [
                {"part_kind": "tool-call", "tool_name": "execute", "tool_call_id": "t1", "args": {}}
            ],
        },
    ]
    cleaned = sanitize_history(dict_history)
    assert len(cleaned) == 1


def test_roundtrip_dirty_save_clean_restore(client):
    """Dirty live history → /history serialize-clean → restore-trim safe."""
    hosted_id = "test-roundtrip"
    history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content="hello")]),
        ModelRequest(parts=[UserPromptPart(content="run x")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="execute", args={}, tool_call_id="t9")]
        ),
    ]
    main.sessions[hosted_id] = _fake_session(history)
    try:
        resp = client.get(f"/agents/{hosted_id}/history")
        assert resp.status_code == 200
        saved = resp.json()["history"]
        assert len(saved) == 3
        assert saved[-1]["kind"] == "request"
        cleaned = main.sanitize_history(saved)
        assert len(cleaned) == 3
    finally:
        main.sessions.pop(hosted_id, None)
