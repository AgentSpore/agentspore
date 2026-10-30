"""Unit tests for the AgentSpore MCP EventBridge.

Focus on pure logic: event ingestion, dedup ring, queue overflow, keepalive
handling. No actual WebSocket or MCP stdio involved.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import websockets
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    TextContent,
)

from agentspore_sdk.mcp_server import EventBridge, _make_server


import pytest_asyncio


@pytest_asyncio.fixture
async def bridge():
    b = EventBridge(api_key="af_test", base_url="http://localhost", queue_max=4)
    yield b
    await b._http.aclose()


@pytest.mark.asyncio
async def test_handle_raw_enqueues_event(bridge):
    await bridge._handle_raw(json.dumps({"type": "dm", "id": "e1", "content": "hi"}))
    assert bridge.queue.qsize() == 1
    assert bridge.events_received == 1
    ev = bridge.queue.get_nowait()
    assert ev["type"] == "dm"
    assert ev["id"] == "e1"


@pytest.mark.asyncio
async def test_binary_websocket_json_is_accepted(bridge):
    await bridge._handle_raw(b'{"type":"dm","id":"binary"}')
    assert bridge.queue.get_nowait()["id"] == "binary"


@pytest.mark.asyncio
async def test_bridge_receives_binary_frame_from_websocket():
    async def handler(websocket):
        await websocket.send(b'{"type":"dm","id":"binary-frame"}')
        await websocket.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        bridge = EventBridge(
            api_key="af_test", base_url=f"http://127.0.0.1:{port}", queue_max=4
        )
        bridge.start()
        try:
            event = await asyncio.wait_for(bridge.queue.get(), timeout=2)
            assert event["id"] == "binary-frame"
        finally:
            await bridge.close()
        assert bridge._task is not None and bridge._task.done()


@pytest.mark.asyncio
async def test_peek_does_not_consume_or_reorder_events(bridge):
    for event_id in ("first", "second", "third"):
        await bridge._handle_raw(json.dumps({"type": "dm", "id": event_id}))
    result = await _make_server(bridge).request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="agentspore_peek_events", arguments={"limit": 2}),
        )
    )
    assert isinstance(result.root, CallToolResult)
    content = result.root.content[0]
    assert isinstance(content, TextContent)
    assert [event["id"] for event in json.loads(content.text)["events"]] == [
        "first", "second"
    ]
    assert [bridge.queue.get_nowait()["id"] for _ in range(3)] == [
        "first", "second", "third"
    ]


@pytest.mark.asyncio
async def test_handle_raw_dedups_by_id(bridge):
    payload = json.dumps({"type": "dm", "id": "dup", "content": "hi"})
    await bridge._handle_raw(payload)
    await bridge._handle_raw(payload)
    await bridge._handle_raw(payload)
    assert bridge.queue.qsize() == 1
    assert bridge.events_received == 1
    assert bridge.events_duplicate == 2


@pytest.mark.asyncio
async def test_handle_raw_dedups_by_event_id_alias(bridge):
    """Events emitted by webhook fallback use event_id; dedup must cover both keys."""
    await bridge._handle_raw(json.dumps({"type": "task", "event_id": "X1"}))
    await bridge._handle_raw(json.dumps({"type": "task", "event_id": "X1"}))
    assert bridge.queue.qsize() == 1
    assert bridge.events_duplicate == 1


@pytest.mark.asyncio
async def test_ping_does_not_enqueue_and_sends_pong(bridge):
    sent = []
    bridge._ws = AsyncMock()
    bridge._ws.send = AsyncMock(side_effect=lambda msg: sent.append(msg))
    await bridge._handle_raw(json.dumps({"type": "ping"}))
    assert bridge.queue.qsize() == 0
    assert sent == [json.dumps({"type": "pong"})]
    assert bridge.events_received == 0


@pytest.mark.asyncio
async def test_internal_acks_are_filtered(bridge):
    for t in ("hello", "pong", "dm_sent"):
        await bridge._handle_raw(json.dumps({"type": t}))
    assert bridge.queue.qsize() == 0
    assert bridge.events_received == 0


@pytest.mark.asyncio
async def test_queue_overflow_drops_oldest_and_increments_counter(bridge):
    # queue_max = 4
    for i in range(1, 5):
        await bridge._handle_raw(json.dumps({"type": "dm", "id": f"e{i}"}))
    assert bridge.queue.qsize() == 4

    # 5th event triggers overflow — oldest (e1) gets dropped, e5 enqueued.
    await bridge._handle_raw(json.dumps({"type": "dm", "id": "e5"}))
    assert bridge.queue.qsize() == 4
    assert bridge.events_dropped == 1

    drained = [bridge.queue.get_nowait()["id"] for _ in range(4)]
    assert drained == ["e2", "e3", "e4", "e5"]


@pytest.mark.asyncio
async def test_dedup_ring_evicts_old_ids_after_1024(bridge):
    """After 1024 distinct ids, the oldest should be forgettable again."""
    for i in range(1024):
        await bridge._handle_raw(json.dumps({"type": "noop", "id": f"x{i}"}))
        # drain so the queue doesn't overflow on unrelated bookkeeping
        try:
            bridge.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    # The very first id should still be in the dedup ring (not yet evicted).
    assert "x0" in bridge._seen_ids

    # Push a new id to push x0 out.
    await bridge._handle_raw(json.dumps({"type": "noop", "id": "x1024"}))
    assert "x0" not in bridge._seen_ids
    # And we should now accept x0 again as a fresh event.
    try:
        bridge.queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    await bridge._handle_raw(json.dumps({"type": "noop", "id": "x0"}))
    assert "x0" in bridge._seen_ids


@pytest.mark.asyncio
async def test_send_command_raises_when_not_connected(bridge):
    # No WS set, _connected never fired → send_command should raise within 5s.
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(
            bridge.send_command({"type": "send_dm", "to": "x", "content": "y"}),
            timeout=6,
        )


@pytest.mark.asyncio
async def test_send_command_writes_to_ws_when_connected(bridge):
    sent: list[str] = []
    bridge._ws = AsyncMock()
    bridge._ws.send = AsyncMock(side_effect=lambda msg: sent.append(msg))
    bridge._connected.set()
    await bridge.send_command({"type": "send_dm", "to": "alice", "content": "hi"})
    assert len(sent) == 1
    assert json.loads(sent[0]) == {"type": "send_dm", "to": "alice", "content": "hi"}


@pytest.mark.parametrize("name", ["agentspore_get_self", "agentspore_update_self"])
async def test_removed_hosted_tools_never_call_http(bridge, name):
    """Legacy hosted tool calls cannot reach a backend or mutate configuration."""
    requests = []
    await bridge._http.aclose()
    bridge._http = httpx.AsyncClient(
        base_url=bridge.base_url,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        ),
    )
    result = await _make_server(bridge).request_handlers[CallToolRequest](
        CallToolRequest(
            method="tools/call", params=CallToolRequestParams(name=name, arguments={})
        )
    )
    assert isinstance(result.root, CallToolResult)
    content = result.root.content
    assert len(content) == 1
    assert isinstance(content[0], TextContent)
    assert json.loads(content[0].text) == {"error": f"unknown tool: {name}"}
    assert not requests
