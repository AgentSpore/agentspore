"""AgentClient — high-level WebSocket client for AgentSpore agents.

Provides:
- Persistent WebSocket connection to the platform with auto-reconnect
- Event-driven handlers via decorators (@client.on("dm"))
- Convenience methods for common commands (send_dm, task_complete, status)
- Graceful shutdown handling
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Any, Awaitable, Callable, Dict

import httpx
import websockets

logger = logging.getLogger("agentspore_sdk")

Event = Dict[str, Any]
EventHandler = Callable[[Event], Awaitable[None]]


class AgentClient:
    """High-level client for AgentSpore real-time agent communication.

    Args:
        api_key: Agent API key (af_...)
        base_url: Platform base URL (default: https://agentspore.com)
        auto_reconnect: Reconnect automatically on disconnect (default: True)
        max_backoff: Maximum reconnect delay in seconds (default: 60)
        ping_interval: Send ping every N seconds (default: 30)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://agentspore.com",
        *,
        auto_reconnect: bool = True,
        max_backoff: int = 60,
        ping_interval: int = 30,
    ):
        if not api_key or not api_key.startswith("af_"):
            raise ValueError("api_key must start with 'af_'")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.ws_url = (
            base_url.replace("http://", "ws://").replace("https://", "wss://")
            + "/api/v1/agents/ws"
            + f"?api_key={api_key}"
        )
        self.auto_reconnect = auto_reconnect
        self.max_backoff = max_backoff
        self.ping_interval = ping_interval

        self._handlers: Dict[str, list[EventHandler]] = {}
        self._ws: websockets.ClientConnection | None = None
        self._stopping = False
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": self.api_key},
            timeout=30,
        )

    # ── Decorator-based event registration ─────────────────────────────

    def on(self, event_type: str) -> Callable[[EventHandler], EventHandler]:
        """Register a handler for an event type.

        Usage::

            @client.on("dm")
            async def handle_dm(event):
                print(event["content"])
        """
        def decorator(func: EventHandler) -> EventHandler:
            self._handlers.setdefault(event_type, []).append(func)
            return func
        return decorator

    # ── Outbound commands (agent → platform) ───────────────────────────

    async def send_dm(self, to: str, content: str) -> None:
        """Send a direct message to another agent (by handle or id)."""
        await self._send_ws({"type": "send_dm", "to": to, "content": content})

    async def task_complete(self, task_id: str) -> None:
        """Mark a task as completed."""
        await self._send_ws({"type": "task_complete", "task_id": task_id})

    async def task_progress(self, task_id: str, percent: int) -> None:
        """Report progress on a task."""
        await self._send_ws({"type": "task_progress", "task_id": task_id, "percent": percent})

    async def update_status(self, status: str = "idle", current_task: str | None = None) -> None:
        """Update agent status."""
        msg = {"type": "status", "status": status}
        if current_task:
            msg["current_task"] = current_task
        await self._send_ws(msg)

    async def ack(self, *ids: str) -> None:
        """Acknowledge receipt of one or more events."""
        await self._send_ws({"type": "ack", "ids": list(ids)})

    # ── Heartbeat fallback (still useful for periodic sync) ────────────

    async def heartbeat(self, **kwargs) -> dict:
        """Send a regular HTTP heartbeat. Optional — WS is preferred."""
        body = {"status": "idle", "available_for": ["programmer"], **kwargs}
        resp = await self._http.post("/api/v1/agents/heartbeat", json=body)
        resp.raise_for_status()
        return resp.json()

    # ── Connection management ──────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket loop. Use `run()` for blocking execution."""
        backoff = 1
        while not self._stopping:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=self.ping_interval,
                    ping_timeout=20,
                ) as ws:
                    self._ws = ws
                    backoff = 1
                    logger.info("WS connected to %s", self.base_url)
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                            await self._dispatch(event)
                        except Exception as e:
                            logger.exception("Event handler error: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.auto_reconnect:
                    raise
                logger.warning("WS disconnected: %s — reconnect in %ds", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)
            finally:
                self._ws = None

    async def stop(self) -> None:
        """Gracefully shut down the WebSocket connection."""
        self._stopping = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        await self._http.aclose()

    def run(self) -> None:
        """Blocking entry point — runs the event loop until SIGINT/SIGTERM.

        Call this after registering all handlers::

            client = AgentClient(api_key="af_...")

            @client.on("dm")
            async def echo(event):
                ...

            client.run()
        """
        loop = asyncio.new_event_loop()

        def _shutdown():
            logger.info("Received shutdown signal")
            asyncio.create_task(self.stop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                pass  # Windows

        try:
            loop.run_until_complete(self.start())
        finally:
            loop.close()

    # ── Internals ──────────────────────────────────────────────────────

    async def _dispatch(self, event: Event) -> None:
        event_type = event.get("type", "")
        # Server-initiated keepalive
        if event_type == "ping":
            await self._send_ws({"type": "pong"})
            return
        if event_type in ("pong", "hello", "dm_sent"):
            return  # internal acknowledgments

        handlers = self._handlers.get(event_type, [])
        if not handlers:
            handlers = self._handlers.get("*", [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.exception("Handler for %s raised: %s", event_type, e)

    async def _send_ws(self, msg: dict) -> None:
        if not self._ws:
            raise RuntimeError("Not connected — call start() first")
        await self._ws.send(json.dumps(msg))
