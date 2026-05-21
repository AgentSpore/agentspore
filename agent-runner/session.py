"""AgentSession class, sessions registry, and session lifecycle helpers."""

import asyncio
import collections
import json
import time

import httpx
from loguru import logger
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
)
from pydantic_ai.tools import DeferredToolResults
from pydantic_ai_backends import DockerSandbox
from pydantic_deep.processors.patch import patch_tool_calls_processor
from sandbox import is_command_safe

from config import get_settings
from content_sanitizer import risk_score, sanitize_for_agent_context

settings = get_settings()

# Imported lazily to avoid circular import with quota module that also
# imports settings at module level.  disk_quota is set by main.py after
# both modules are fully initialised.
disk_quota = None  # set by main.py: session.disk_quota = disk_quota


def sanitize_history(messages: list) -> list:
    """Sanitize message_history before persistence/restore.

    Drops trailing ModelResponse with orphan ToolCallParts (no matching
    ToolReturnParts in any subsequent ModelRequest), then runs
    pydantic-deep's patch_tool_calls_processor to inject synthetic
    ToolReturnParts for any remaining mid-history orphans.

    Without this, an aborted/timed-out tool call leaves an orphan
    ToolCallPart in saved history. On next start, pydantic-deep injects
    a synthetic "Tool call was cancelled." which the agent reads as
    real evidence that tools/network are blocked, leading to confused
    responses to the user.
    """
    if not messages:
        return messages

    cleaned = list(messages)
    # Trim trailing orphan ToolCallParts (handles both ModelMessage objects
    # and serialized dict form for restored history).
    while cleaned:
        last = cleaned[-1]
        if isinstance(last, ModelResponse):
            has_orphan_tool = any(isinstance(p, ToolCallPart) for p in last.parts)
        elif isinstance(last, dict) and last.get("kind") == "response":
            has_orphan_tool = any(
                isinstance(p, dict) and p.get("part_kind") == "tool-call"
                for p in (last.get("parts") or [])
            )
        else:
            break
        if not has_orphan_tool:
            break
        cleaned = cleaned[:-1]

    # Convert restored dict-form messages into ModelMessage objects.
    # pydantic-ai-slim 1.93+ accesses ``state.conversation_id`` on each
    # message during ``agent.run()``; raw dicts raise
    # ``AttributeError("'dict' object has no attribute 'conversation_id'")``
    # and surface as HTTP 500 on the chat endpoint.
    if cleaned and isinstance(cleaned[0], dict):
        try:
            cleaned = ModelMessagesTypeAdapter.validate_python(cleaned)
        except Exception as e:
            logger.warning(
                "History deserialize failed for {} messages, dropping history: {}",
                len(cleaned), e,
            )
            return []

    try:
        return patch_tool_calls_processor(cleaned)
    except Exception as e:
        logger.warning("History sanitize fallback (patch failed): {}", e)
        return cleaned


class AgentSession:
    """Holds a running agent's sandbox, agent instance, message history, and heartbeat task."""

    def __init__(self, hosted_id: str, sandbox: DockerSandbox, agent, deps,
                 api_key: str = "", heartbeat_seconds: int = 3600,
                 auto_react: bool = True, max_reactions_per_minute: int = 10,
                 agent_handle: str = "", model: str = ""):
        self.hosted_id = hosted_id
        self.sandbox = sandbox
        self.agent = agent
        self.deps = deps
        self.message_history: list = []
        self.api_key = api_key
        self.heartbeat_seconds = heartbeat_seconds
        self.agent_handle: str = agent_handle
        self.model: str = model
        self.heartbeat_task: asyncio.Task | None = None
        self.last_activity: float = time.time()
        self.chat_lock = asyncio.Lock()

        # Disk quota background watcher
        self.quota_watcher_task: asyncio.Task | None = None

        # Real-time WebSocket state
        self.ws_task: asyncio.Task | None = None
        self.ws_connected = False
        self.auto_react = auto_react
        self.max_reactions_per_minute = max_reactions_per_minute
        self._reaction_timestamps: list[float] = []
        # Idempotency: ring buffer of recent event ids to drop replays
        self._seen_event_ids: collections.deque = collections.deque(maxlen=512)

    def touch(self):
        """Update last activity timestamp."""
        self.last_activity = time.time()

    def is_idle(self) -> bool:
        """Check if agent has been idle longer than the configured timeout."""
        return (time.time() - self.last_activity) > settings.idle_timeout_seconds

    def start_heartbeat(self):
        """Start periodic heartbeat for this agent."""
        if self.api_key and self.heartbeat_seconds > 0:
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def stop_heartbeat(self):
        """Stop heartbeat task."""
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None

    async def _heartbeat_loop(self):
        """Send heartbeat to AgentSpore platform at configured interval."""
        await self._send_heartbeat()
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            await self._send_heartbeat()

    async def _send_heartbeat(self):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{settings.agentspore_url}/api/v1/agents/heartbeat",
                    headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
                    json={"status": "idle", "available_for": ["programmer"]},
                )
                if resp.status_code == 200:
                    logger.debug("Heartbeat OK for {}", self.hosted_id)
                    # Inject memory_context from platform into agent's context
                    data = resp.json()
                    memory_ctx = data.get("memory_context", [])
                    if memory_ctx and isinstance(memory_ctx, list):
                        sanitized_items = [
                            sanitize_for_agent_context(str(m), max_len=200)
                            for m in memory_ctx[:5]
                        ]
                        ctx_text = "\n".join(sanitized_items)
                        if ctx_text.strip():
                            raw_combined = "\n".join(str(m) for m in memory_ctx[:5])
                            score = risk_score(raw_combined)
                            if score > 50:
                                logger.warning(
                                    "High-risk content in heartbeat memory_context score={}: {!r}",
                                    score, raw_combined[:100],
                                )
                            self.message_history.append(
                                ModelRequest(parts=[SystemPromptPart(
                                    content=f"[Platform memory update]\n{ctx_text}"
                                )])
                            )
                            logger.debug("Injected {} memory items for {}", len(memory_ctx), self.hosted_id)
                else:
                    logger.warning("Heartbeat {} for {}: {}", resp.status_code, self.hosted_id, resp.text[:100])
        except Exception as e:
            logger.warning("Heartbeat failed for {}: {}", self.hosted_id, e)

    # ── Real-time WebSocket ─────────────────────────────────────────────

    def start_websocket(self):
        """Connect to platform WebSocket for real-time events."""
        if self.api_key and not self.ws_task:
            self.ws_task = asyncio.create_task(self._websocket_loop())

    def stop_websocket(self):
        """Cancel WebSocket task."""
        if self.ws_task:
            self.ws_task.cancel()
            self.ws_task = None
            self.ws_connected = False

    async def _websocket_loop(self):
        """Maintain a persistent WebSocket connection with reconnect/backoff."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets package not installed; real-time disabled for {}", self.hosted_id)
            return

        # Convert http(s):// → ws(s)://
        ws_base = settings.agentspore_url.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{ws_base}/api/v1/agents/ws?api_key={self.api_key}"

        backoff = 1
        max_backoff = 60
        while True:
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=20) as ws:
                    self.ws_connected = True
                    backoff = 1
                    logger.info("WS connected for {}", self.hosted_id)
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                            await self._handle_platform_event(event, ws)
                        except Exception as e:
                            logger.warning("WS event handling error for {}: {}", self.hosted_id, e)
            except asyncio.CancelledError:
                self.ws_connected = False
                return
            except Exception as e:
                self.ws_connected = False
                logger.debug("WS disconnect for {}, retry in {}s: {}", self.hosted_id, backoff, e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    def start_quota_watcher(self):
        """Start periodic disk quota monitoring for this agent."""
        if disk_quota is not None and disk_quota.is_enabled() and not self.quota_watcher_task:
            self.quota_watcher_task = asyncio.create_task(
                disk_quota.watcher_loop(self.hosted_id)
            )

    def stop_quota_watcher(self):
        """Cancel disk quota watcher task."""
        if self.quota_watcher_task:
            self.quota_watcher_task.cancel()
            self.quota_watcher_task = None

    async def _handle_platform_event(self, event: dict, ws):
        """Handle an event received from the platform.

        Injects the event into the agent's message history as a system message.
        Optionally triggers an automatic reaction (agent.iter()) if auto_react is enabled.
        """
        event_type = event.get("type")

        # Idempotency: drop replays of the same event id
        eid = event.get("id") or event.get("event_id")
        if eid:
            if eid in self._seen_event_ids:
                logger.debug("Dropping duplicate event {} for {}", eid, self.hosted_id)
                return
            self._seen_event_ids.append(eid)

        # Server-initiated keepalive
        if event_type == "ping":
            try:
                await ws.send(json.dumps({"type": "pong"}))
            except Exception:
                pass
            return
        if event_type == "pong":
            return
        if event_type == "hello":
            logger.debug("WS hello for {}: {}", self.hosted_id, event.get("agent_name"))
            return

        # Format event as a system message and inject into context
        formatted = self._format_event_for_context(event)
        if not formatted:
            return

        self.message_history.append(
            ModelRequest(parts=[SystemPromptPart(content=formatted)])
        )
        logger.info("Real-time event for {}: type={}", self.hosted_id, event_type)

        # Auto-react if enabled and rate limit allows
        if self.auto_react and self._can_react() and event_type in ("dm", "task", "mention", "rental_message"):
            asyncio.create_task(self._auto_react(event))

    def _format_event_for_context(self, event: dict) -> str | None:
        """Convert a platform event to a system prompt fragment.

        All user-supplied strings are sanitized via sanitize_for_agent_context()
        before injection to neutralize bidirectional override characters and
        mirrored Unicode used in prompt-injection attacks.
        """
        et = event.get("type")
        if et == "dm":
            sender = event.get("from_name") or event.get("from") or "unknown"
            raw_content = event.get("content", "")
            score = risk_score(raw_content)
            if score > 50:
                logger.warning(
                    "High-risk content in event type='{}' score={}: {!r}",
                    et, score, raw_content[:100],
                )
            content = sanitize_for_agent_context(raw_content)
            sender = sanitize_for_agent_context(str(sender), max_len=100)
            return f"[Real-time DM from {sender}]\n{content}"
        if et == "task":
            title = sanitize_for_agent_context(event.get("title", ""), max_len=200)
            priority = sanitize_for_agent_context(event.get("priority", "normal"), max_len=50)
            return f"[Real-time task assigned: {title}]\nPriority: {priority}"
        if et == "notification":
            title = sanitize_for_agent_context(event.get("title", ""), max_len=200)
            task_type = sanitize_for_agent_context(event.get("task_type", ""), max_len=100)
            return f"[Real-time notification: {title}]\nType: {task_type}"
        if et == "mention":
            from_who = sanitize_for_agent_context(event.get("from", ""), max_len=100)
            raw_context = event.get("context", "")
            score = risk_score(raw_context)
            if score > 50:
                logger.warning(
                    "High-risk content in event type='{}' score={}: {!r}",
                    et, score, raw_context[:100],
                )
            context = sanitize_for_agent_context(raw_context)
            return f"[You were mentioned by {from_who}]\nContext: {context}"
        if et == "rental_message":
            raw_content = event.get("content", "")
            score = risk_score(raw_content)
            if score > 50:
                logger.warning(
                    "High-risk content in event type='{}' score={}: {!r}",
                    et, score, raw_content[:100],
                )
            content = sanitize_for_agent_context(raw_content)
            return f"[Real-time rental message]\n{content}"
        if et == "memory_context":
            items = event.get("items", [])
            if not items:
                return None
            ctx = "\n".join(
                sanitize_for_agent_context(str(i), max_len=200) for i in items[:5]
            )
            return f"[Platform memory update]\n{ctx}"
        return None

    def _can_react(self) -> bool:
        """Rate limit: max N auto-reactions per minute."""
        now = time.time()
        # Drop timestamps older than 60s
        self._reaction_timestamps = [t for t in self._reaction_timestamps if now - t < 60]
        if len(self._reaction_timestamps) >= self.max_reactions_per_minute:
            logger.warning("Reaction rate limit hit for {}", self.hosted_id)
            return False
        return True

    async def _auto_react(self, event: dict):
        """Trigger agent.run() to react to a platform event automatically."""
        if self.chat_lock.locked():
            logger.debug("Auto-react skipped for {}: chat busy", self.hosted_id)
            return

        async with self.chat_lock:
            self._reaction_timestamps.append(time.time())
            self.touch()
            try:
                # Trigger a non-streaming run with empty user message —
                # the system message we just appended provides the trigger.
                # The model will see "[Real-time DM from X]" and respond.
                result = await self.agent.run(
                    "Respond to the latest real-time event in your context.",
                    deps=self.deps,
                    message_history=self.message_history,
                    model_settings={"timeout": settings.chat_timeout},
                )
                self.message_history = sanitize_history(result.all_messages())[-100:]

                # Auto-approve deferred tool calls (execute is interrupt_on by default)
                max_approvals = 10
                while isinstance(result.output, DeferredToolRequests) and max_approvals > 0:
                    deferred = result.output
                    approvals: dict[str, bool] = {}
                    for tc in deferred.approvals:
                        if tc.tool_name == "execute":
                            cmd = tc.args.get("command", "") if isinstance(tc.args, dict) else str(tc.args)
                            safe, reason = is_command_safe(cmd)
                            if not safe:
                                logger.warning("Auto-react blocked unsafe command: {} ({})", cmd, reason)
                                approvals[tc.tool_call_id] = False
                                continue
                        approvals[tc.tool_call_id] = True
                    logger.info("Auto-react: approving {} deferred tools for {}", sum(v for v in approvals.values()), self.hosted_id)
                    result = await self.agent.run(
                        deferred_tool_results=DeferredToolResults(approvals=approvals),
                        deps=self.deps,
                        message_history=result.all_messages(),
                        model_settings={"timeout": settings.chat_timeout},
                    )
                    self.message_history = sanitize_history(result.all_messages())[-100:]
                    max_approvals -= 1

                logger.info("Auto-reacted to {} for {}", event.get("type"), self.hosted_id)
            except Exception as e:
                logger.warning("Auto-react failed for {}: {}", self.hosted_id, e)


# Active agent sessions
sessions: dict[str, AgentSession] = {}


def cleanup_all_sessions():
    """Stop all Docker containers, heartbeats, and websockets on shutdown."""
    for hid, session in list(sessions.items()):
        session.stop_heartbeat()
        session.stop_websocket()
        session.stop_quota_watcher()
        try:
            session.sandbox.stop()
            logger.info("Cleaned up container for {}", hid)
        except Exception as e:
            logger.warning("Cleanup error for {}: {}", hid, e)
    sessions.clear()


async def idle_cleanup_loop():
    """Periodically stop agents that have been idle too long."""
    while True:
        await asyncio.sleep(300)  # check every 5 min
        idle_agents = [hid for hid, s in sessions.items() if s.is_idle()]
        for hid in idle_agents:
            session = sessions.pop(hid, None)
            if session:
                session.stop_heartbeat()
                session.stop_websocket()
                session.stop_quota_watcher()
                try:
                    session.sandbox.stop()
                except Exception:
                    pass
                logger.info("Auto-stopped idle agent {} (idle {}s)", hid, int(time.time() - session.last_activity))
                # Notify platform
                try:
                    params = {"key": settings.runner_key}
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{settings.agentspore_url}/api/v1/hosted-agents/{hid}/idle-stopped",
                            params=params,
                        )
                except Exception:
                    pass
