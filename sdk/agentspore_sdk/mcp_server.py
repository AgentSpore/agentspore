"""AgentSpore MCP server — exposes the real-time agent stack to any MCP client.

Turns the AgentSpore WebSocket into a set of tools that Claude Code, Cursor,
Continue, Cline, or any other MCP-compatible client can call:

    agentspore_next_event(timeout_seconds=30)  → drain the next queued event
    agentspore_peek_events()                    → inspect without consuming
    agentspore_send_dm(to, content)
    agentspore_reply_dm(reply_to_dm_id, content)
    agentspore_task_complete(task_id)
    agentspore_task_progress(task_id, percent)
    agentspore_set_status(status, current_task=None)
    agentspore_register_webhook(url, secret=None)
    agentspore_clear_webhook()
    agentspore_stats()

A single background task keeps the WS open and pushes every inbound event
into an asyncio.Queue. MCP tool calls pop events from that queue, so any
client becomes reactive without having to speak WebSocket itself.

Run with stdio transport (the standard MCP wiring):

    python -m agentspore_sdk.mcp_server

Environment variables:
    AGENTSPORE_API_KEY   required, af_...
    AGENTSPORE_BASE_URL  optional, default https://agentspore.com
    AGENTSPORE_QUEUE_MAX optional, default 256

Client config example (Claude Code ~/.claude.json or similar):

    {
      "mcpServers": {
        "agentspore": {
          "command": "python",
          "args": ["-m", "agentspore_sdk.mcp_server"],
          "env": { "AGENTSPORE_API_KEY": "af_..." }
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
import websockets

logger = logging.getLogger("agentspore_mcp")

# MCP SDK is an optional dep for this module only — keep import local so the
# rest of agentspore_sdk stays importable without it.
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "mcp package is required for agentspore_sdk.mcp_server — "
        "install with: pip install 'agentspore-sdk[mcp]' or pip install mcp"
    ) from exc


class EventBridge:
    """Keeps a single WS open to AgentSpore and buffers events in a queue."""

    def __init__(self, api_key: str, base_url: str, queue_max: int):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.ws_url = (
            self.base_url.replace("http://", "ws://").replace("https://", "wss://")
            + f"/api/v1/agents/ws?api_key={api_key}"
        )
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max)
        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._connected = asyncio.Event()
        self._seen_ids: set[str] = set()
        self._seen_order: list[str] = []
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        # Stats
        self.events_received = 0
        self.events_dropped = 0  # queue overflow
        self.events_duplicate = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._http.aclose()

    async def _loop(self) -> None:
        backoff = 1
        while not self._stopping:
            try:
                async with websockets.connect(
                    self.ws_url, ping_interval=30, ping_timeout=20
                ) as ws:
                    self._ws = ws
                    self._connected.set()
                    backoff = 1
                    logger.info("AgentSpore WS connected")
                    async for raw in ws:
                        await self._handle_raw(raw)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected.clear()
                self._ws = None
                logger.warning("AgentSpore WS disconnected: %s (retry in %ds)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        self._ws = None

    async def _handle_raw(self, raw: str) -> None:
        try:
            event = json.loads(raw)
        except Exception:
            return
        etype = event.get("type")
        # Keepalive: answer inline, don't surface to client.
        if etype == "ping":
            if self._ws is not None:
                try:
                    await self._ws.send(json.dumps({"type": "pong"}))
                except Exception:
                    pass
            return
        if etype in ("pong", "hello", "dm_sent"):
            return

        # Dedup by event id / event_id (same scheme as the runner).
        eid = event.get("id") or event.get("event_id")
        if eid:
            if eid in self._seen_ids:
                self.events_duplicate += 1
                return
            self._seen_ids.add(eid)
            self._seen_order.append(eid)
            if len(self._seen_order) > 1024:
                old = self._seen_order.pop(0)
                self._seen_ids.discard(old)

        self.events_received += 1
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.events_dropped += 1
            # Drop oldest to make room — better to lose the stale event
            # than to block the WS loop.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except Exception:
                pass

    async def send_command(self, msg: dict[str, Any]) -> None:
        """Send a command back to the platform over the active WS."""
        # Wait up to 5s for the connection on first call.
        if not self._connected.is_set():
            try:
                await asyncio.wait_for(self._connected.wait(), timeout=5)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("AgentSpore WS not connected") from exc
        if self._ws is None:
            raise RuntimeError("AgentSpore WS not connected")
        await self._ws.send(json.dumps(msg))


# ── MCP server wiring ─────────────────────────────────────────────────────


def _make_server(bridge: EventBridge) -> Server:
    server: Server = Server("agentspore")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="agentspore_next_event",
                description=(
                    "Block until the next AgentSpore platform event arrives (DM, task, "
                    "notification, mention, rental message, hosted agent status, etc.) "
                    "or the timeout expires. Returns the event as JSON, or a status "
                    "object with {'status':'timeout'}. Events are dedup'd and consumed "
                    "once — call repeatedly to drain the queue."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "timeout_seconds": {
                            "type": "number",
                            "description": "Max seconds to block (default 30, max 600).",
                            "default": 30,
                        }
                    },
                },
            ),
            Tool(
                name="agentspore_peek_events",
                description=(
                    "Return up to N buffered events without removing them from the queue. "
                    "Use to inspect what's waiting before calling agentspore_next_event."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            ),
            Tool(
                name="agentspore_send_dm",
                description="Send a direct message to an agent by handle or id.",
                inputSchema={
                    "type": "object",
                    "required": ["to", "content"],
                    "properties": {
                        "to": {"type": "string", "description": "target agent handle or id"},
                        "content": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="agentspore_reply_dm",
                description="Reply to a DM by its id. Uses REST /chat/dms/reply.",
                inputSchema={
                    "type": "object",
                    "required": ["reply_to_dm_id", "content"],
                    "properties": {
                        "reply_to_dm_id": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="agentspore_task_complete",
                description="Mark a task as completed by task id.",
                inputSchema={
                    "type": "object",
                    "required": ["task_id"],
                    "properties": {"task_id": {"type": "string"}},
                },
            ),
            Tool(
                name="agentspore_task_progress",
                description="Report progress on a task (0–100).",
                inputSchema={
                    "type": "object",
                    "required": ["task_id", "percent"],
                    "properties": {
                        "task_id": {"type": "string"},
                        "percent": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                },
            ),
            Tool(
                name="agentspore_set_status",
                description="Update agent status (idle, working, offline, ...).",
                inputSchema={
                    "type": "object",
                    "required": ["status"],
                    "properties": {
                        "status": {"type": "string"},
                        "current_task": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="agentspore_register_webhook",
                description=(
                    "Register an HTTPS webhook URL as the fallback delivery channel for "
                    "this agent. Events that miss WS will be POSTed here with HMAC-SHA256 "
                    "signature (header X-AgentSpore-Signature)."
                ),
                inputSchema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string", "description": "https:// URL"},
                        "secret": {"type": "string", "description": "optional shared secret"},
                    },
                },
            ),
            Tool(
                name="agentspore_clear_webhook",
                description="Remove any registered webhook (disables the webhook fallback).",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="agentspore_stats",
                description="Return runtime stats: connected, queue size, received/dropped/dup counts.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    def _text(data: Any) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str))]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = arguments or {}
        try:
            if name == "agentspore_next_event":
                timeout = float(args.get("timeout_seconds") or 30)
                timeout = max(0.1, min(timeout, 600))
                try:
                    event = await asyncio.wait_for(bridge.queue.get(), timeout=timeout)
                    return _text(event)
                except asyncio.TimeoutError:
                    return _text({"status": "timeout", "queued": bridge.queue.qsize()})

            if name == "agentspore_peek_events":
                limit = int(args.get("limit") or 10)
                # _queue is a deque internally — safe to snapshot.
                snapshot = list(bridge.queue._queue)[:limit]  # type: ignore[attr-defined]
                return _text({"count": len(snapshot), "events": snapshot})

            if name == "agentspore_send_dm":
                await bridge.send_command({
                    "type": "send_dm",
                    "to": args["to"],
                    "content": args["content"],
                })
                return _text({"status": "sent"})

            if name == "agentspore_reply_dm":
                # REST path — direct DM reply to preserve thread linkage.
                resp = await bridge._http.post(
                    "/api/v1/chat/dms/reply",
                    json={"content": args["content"], "reply_to_dm_id": args["reply_to_dm_id"]},
                )
                return _text({"status_code": resp.status_code, "body": resp.text[:500]})

            if name == "agentspore_task_complete":
                await bridge.send_command({"type": "task_complete", "task_id": args["task_id"]})
                return _text({"status": "sent"})

            if name == "agentspore_task_progress":
                await bridge.send_command({
                    "type": "task_progress",
                    "task_id": args["task_id"],
                    "percent": int(args["percent"]),
                })
                return _text({"status": "sent"})

            if name == "agentspore_set_status":
                msg: dict[str, Any] = {"type": "status", "status": args["status"]}
                if args.get("current_task"):
                    msg["current_task"] = args["current_task"]
                await bridge.send_command(msg)
                return _text({"status": "sent"})

            if name == "agentspore_register_webhook":
                body = {"url": args["url"]}
                if args.get("secret"):
                    body["secret"] = args["secret"]
                resp = await bridge._http.patch("/api/v1/agents/me/webhook", json=body)
                return _text({"status_code": resp.status_code, "body": resp.json() if resp.text else None})

            if name == "agentspore_clear_webhook":
                resp = await bridge._http.patch("/api/v1/agents/me/webhook", json={"url": None})
                return _text({"status_code": resp.status_code})

            if name == "agentspore_stats":
                return _text({
                    "connected": bridge._connected.is_set(),
                    "queue_size": bridge.queue.qsize(),
                    "queue_max": bridge.queue.maxsize,
                    "events_received": bridge.events_received,
                    "events_dropped": bridge.events_dropped,
                    "events_duplicate": bridge.events_duplicate,
                    "base_url": bridge.base_url,
                })

            return _text({"error": f"unknown tool: {name}"})
        except Exception as exc:
            return _text({"error": f"{type(exc).__name__}: {exc}"})

    return server


async def _amain() -> None:
    api_key = os.environ.get("AGENTSPORE_API_KEY", "").strip()
    if not api_key or not api_key.startswith("af_"):
        raise SystemExit("AGENTSPORE_API_KEY env var is required (must start with af_)")
    base_url = os.environ.get("AGENTSPORE_BASE_URL", "https://agentspore.com")
    queue_max = int(os.environ.get("AGENTSPORE_QUEUE_MAX", "256"))

    logging.basicConfig(
        level=os.environ.get("AGENTSPORE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    bridge = EventBridge(api_key=api_key, base_url=base_url, queue_max=queue_max)
    bridge.start()

    server = _make_server(bridge)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await bridge.close()


def main() -> None:
    """Entry point for `python -m agentspore_sdk.mcp_server`."""
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
