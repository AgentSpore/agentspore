"""Agent Runner Service — manages pydantic-deepagents in Docker containers.

Runs on the infra server (178.154.244.194). The main AgentSpore backend
communicates with this service to create, start, stop, and chat with
hosted agents. Each agent runs in an isolated Docker container with
its own heartbeat schedule.

v0.3.0 — streaming chat, include_execute, idle cleanup
"""

import asyncio
import atexit
import collections
import json
import os
import secrets
import signal
import sys
import time
import uvicorn
import docker
import httpx

from contextlib import asynccontextmanager

from starlette.responses import JSONResponse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic_ai.messages import ModelRequest, SystemPromptPart, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, PartStartEvent
from pydantic_ai import DeferredToolRequests, FunctionToolResultEvent
from pydantic_ai.tools import DeferredToolResults
from pydantic_deep import create_deep_agent, DeepAgent, DeepAgentDeps, InMemoryCheckpointStore
from pydantic_ai_backends import DockerSandbox

from config import get_settings
from loguru import logger

settings = get_settings()

# Expose LLM credentials as env vars for pydantic-ai / openai client
if settings.openai_api_key:
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
if settings.openai_base_url:
    os.environ.setdefault("OPENAI_BASE_URL", settings.openai_base_url)
if settings.docker_host:
    os.environ.setdefault("DOCKER_HOST", settings.docker_host)


class SecureDockerSandbox(DockerSandbox):
    """DockerSandbox with security hardening: resource limits, non-root user, capability drops."""

    def _ensure_container(self) -> None:
        if self._container is not None:
            return

        client = docker.from_env()

        image = self._ensure_runtime_image(client)

        env_vars = {}
        if self._runtime and self._runtime.env_vars:
            env_vars = self._runtime.env_vars

        docker_volumes: dict[str, dict[str, str]] = {}
        for host_path, container_path in self._volumes.items():
            docker_volumes[host_path] = {"bind": container_path, "mode": "rw"}

        self._container = client.containers.run(
            image,
            command="sleep infinity",
            detach=True,
            working_dir=self._work_dir,
            auto_remove=self._auto_remove,
            environment=env_vars,
            volumes=docker_volumes if docker_volumes else None,
            # Security hardening
            mem_limit=settings.container_mem_limit,
            cpu_quota=settings.container_cpu_quota,
            pids_limit=settings.container_pids_limit,
            user=settings.container_user,
            cap_drop=["ALL"],
            cap_add=["NET_RAW"],  # needed for DNS resolution in container
            security_opt=["no-new-privileges"],
        )


BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":()", "fork",
    "shutdown", "reboot", "halt", "poweroff",
    "chmod 777 /", "chown root",
    "/etc/shadow", "/etc/passwd",
]


def is_command_safe(command: str) -> tuple[bool, str]:
    """Check if an execute command is safe to auto-approve."""
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return False, f"Blocked command pattern: {blocked}"
    return True, ""


class AgentSession:
    """Holds a running agent's sandbox, agent instance, message history, and heartbeat task."""

    def __init__(self, hosted_id: str, sandbox: DockerSandbox, agent, deps: DeepAgentDeps,
                 api_key: str = "", heartbeat_seconds: int = 3600,
                 auto_react: bool = True, max_reactions_per_minute: int = 10):
        self.hosted_id = hosted_id
        self.sandbox = sandbox
        self.agent = agent
        self.deps = deps
        self.message_history: list = []
        self.api_key = api_key
        self.heartbeat_seconds = heartbeat_seconds
        self.heartbeat_task: asyncio.Task | None = None
        self.last_activity: float = time.time()
        self.chat_lock = asyncio.Lock()

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
                        ctx_text = "\n".join(str(m)[:200] for m in memory_ctx[:5])
                        if ctx_text.strip():
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
        """Convert a platform event to a system prompt fragment."""
        et = event.get("type")
        if et == "dm":
            sender = event.get("from_name") or event.get("from") or "unknown"
            return f"[Real-time DM from {sender}]\n{event.get('content', '')}"
        if et == "task":
            return f"[Real-time task assigned: {event.get('title', '')}]\nPriority: {event.get('priority', 'normal')}"
        if et == "notification":
            return f"[Real-time notification: {event.get('title', '')}]\nType: {event.get('task_type', '')}"
        if et == "mention":
            return f"[You were mentioned by {event.get('from', '')}]\nContext: {event.get('context', '')}"
        if et == "rental_message":
            return f"[Real-time rental message]\n{event.get('content', '')}"
        if et == "memory_context":
            items = event.get("items", [])
            if not items:
                return None
            ctx = "\n".join(str(i)[:200] for i in items[:5])
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
                self.message_history = result.all_messages()[-100:]
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
                try:
                    session.sandbox.stop()
                except Exception:
                    pass
                logger.info("Auto-stopped idle agent {} (idle {}s)", hid, int(time.time() - session.last_activity))
                # Notify platform
                try:
                    params = {"key": settings.runner_key} if settings.runner_key else {}
                    async with httpx.AsyncClient(timeout=5) as client:
                        await client.post(
                            f"{settings.agentspore_url}/api/v1/hosted-agents/{hid}/idle-stopped",
                            params=params,
                        )
                except Exception:
                    pass


async def restore_running_agents():
    """On startup, restore agents that were running before runner restart."""
    try:
        params = {"key": settings.runner_key} if settings.runner_key else {}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.agentspore_url}/api/v1/hosted-agents/running",
                params=params,
            )
            if resp.status_code != 200:
                logger.info("No running agents to restore ({})", resp.status_code)
                return
            agents = resp.json()

        for a in agents:
            hosted_id = a.get("id", "")
            if hosted_id in sessions:
                continue
            try:
                body = StartRequest(
                    agent_id=a.get("agent_id", ""),
                    system_prompt=a.get("system_prompt", ""),
                    model=a.get("model", settings.default_model),
                    api_key=a.get("agent_api_key", ""),
                    heartbeat_seconds=a.get("heartbeat_seconds", settings.default_heartbeat_seconds),
                    files=a.get("files", []),
                )
                await start_agent(hosted_id, body)
                logger.info("Restored agent {}", hosted_id)
            except Exception as e:
                logger.warning("Failed to restore {}: {}", hosted_id, e)

        logger.info("Restored {} agents", len(agents))
    except Exception as e:
        logger.info("Agent restore skipped: {}", e)


@asynccontextmanager
async def lifespan(app):
    await restore_running_agents()
    cleanup_task = asyncio.create_task(idle_cleanup_loop())
    yield
    cleanup_task.cancel()
    cleanup_all_sessions()


app = FastAPI(title="Agent Runner", version="0.3.0", lifespan=lifespan)


@app.middleware("http")
async def verify_runner_key(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if settings.runner_key:
        key = request.headers.get("X-Runner-Key", "")
        if not key or not secrets.compare_digest(key, settings.runner_key):
            return JSONResponse({"detail": "Unauthorized"}, status_code=403)
    return await call_next(request)


signal.signal(signal.SIGTERM, lambda *_: (cleanup_all_sessions(), sys.exit(0)))
atexit.register(cleanup_all_sessions)


# ── Request/Response models ──


class StartRequest(BaseModel):
    agent_id: str
    system_prompt: str
    model: str = "mistralai/mistral-nemo"
    runtime: str = "python-minimal"
    memory_limit_mb: int = 256
    files: list[dict] = []
    api_key: str = ""
    heartbeat_seconds: int = 3600
    message_history: list[dict] = []
    context_max_tokens: int = 128_000
    stuck_loop_detection: bool = False


class ChatRequest(BaseModel):
    content: str


class ActionResponse(BaseModel):
    status: str
    message: str = ""
    container_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict] = []
    thinking: str | None = None


# ── Helper: extract final response data from result ──


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


# ── Endpoints ──


@app.post("/agents/{hosted_id}/start", response_model=ActionResponse)
async def start_agent(hosted_id: str, body: StartRequest):
    """Start a hosted agent in a Docker container with its own heartbeat."""
    if hosted_id in sessions:
        raise HTTPException(400, "Agent already running")

    if len(sessions) >= settings.max_agents:
        raise HTTPException(503, f"Max {settings.max_agents} agents reached")

    workspace = settings.workspace_root / hosted_id
    workspace.mkdir(parents=True, exist_ok=True)

    for subdir in [".deep/memory/main", ".deep/checkpoints", ".deep/plans", "skills"]:
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    for f in body.files:
        fp = f.get("file_path", "")
        if not fp:
            continue
        file_path = workspace / fp
        if not str(file_path).startswith(str(workspace)):
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f.get("content", "") or "", encoding="utf-8")

    # Write AGENT.md with system prompt + platform credentials
    agent_md_parts = [body.system_prompt]
    if body.api_key or body.agent_id:
        agent_md_parts.append("\n\n---\n## Platform Credentials (DO NOT share with users)\n")
        agent_md_parts.append(f"- **Platform URL**: {settings.agentspore_url}\n")
        if body.agent_id:
            agent_md_parts.append(f"- **Agent ID**: `{body.agent_id}`\n")
        if body.api_key:
            agent_md_parts.append(f"- **API Key**: `{body.api_key}`\n")
        agent_md_parts.append(f"- **Auth Header**: `X-API-Key: {body.api_key}`\n")
        agent_md_parts.append("\nUse these credentials for all AgentSpore API calls (heartbeat, projects, chat, etc).\n")
    (workspace / "AGENT.md").write_text("".join(agent_md_parts), encoding="utf-8")

    memory_file = workspace / ".deep" / "memory" / "main" / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("", encoding="utf-8")

    # Fetch SKILL.md from platform if not present
    skill_file = workspace / "SKILL.md"
    if not skill_file.exists():
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{settings.agentspore_url}/skill.md")
                if r.status_code == 200:
                    skill_file.write_text(r.text, encoding="utf-8")
                    logger.info("Downloaded SKILL.md for agent {}", hosted_id)
        except Exception as e:
            logger.warning("Could not fetch SKILL.md: {}", e)

    # Ensure workspace is writable by container user (1000:1000)
    # Requires root on the host — skip gracefully in local dev
    # Skip broken symlinks (e.g. venv/bin/python3) to avoid FileNotFoundError

    if os.getuid() == 0:
        for root, dirs, files in os.walk(workspace):
            try:
                os.chown(root, 1000, 1000)
            except OSError:
                pass
            for f in files:
                fpath = os.path.join(root, f)
                if os.path.exists(fpath):
                    try:
                        os.chown(fpath, 1000, 1000)
                    except OSError:
                        pass
    else:
        for root, dirs, files in os.walk(workspace):
            try:
                os.chmod(root, 0o777)
            except OSError:
                pass
            for f in files:
                fpath = os.path.join(root, f)
                if os.path.exists(fpath):
                    try:
                        os.chmod(fpath, 0o666)
                    except OSError:
                        pass

    sandbox = SecureDockerSandbox(
        image=settings.docker_image,
        work_dir="/workspace",
        volumes={str(workspace): "/workspace"},
        auto_remove=True,
    )

    # Eviction limit = ~5% of model context window (min 5K)
    # Large outputs beyond this limit are automatically truncated
    ctx = body.context_max_tokens or 128000
    eviction_limit = max(5000, ctx // 20)

    # Load agent from workspace agent.yaml if exists, otherwise use defaults
    agent_yaml = workspace / "agent.yaml"
    if agent_yaml.exists():
        logger.info("Loading agent spec from {}", agent_yaml)
        agent, deps = DeepAgent.from_file(
            str(agent_yaml),
            model=f"openai:{body.model}",
            instructions=body.system_prompt,
            backend=sandbox,
            output_type=[str, DeferredToolRequests],
            cost_budget_usd=settings.default_budget_usd,
            context_manager_max_tokens=body.context_max_tokens,
            eviction_token_limit=eviction_limit,
        )
    else:
        agent = create_deep_agent(
            model=f"openai:{body.model}",
            instructions=body.system_prompt,
            output_type=[str, DeferredToolRequests],
            include_todo=True,
            include_filesystem=True,
            include_execute=True,
            include_subagents=False,
            include_skills=True,
            include_memory=True,
            memory_dir="/workspace/.deep/memory",
            include_plan=True,
            web_search=bool(os.environ.get("TAVILY_API_KEY")),
            web_fetch=bool(os.environ.get("TAVILY_API_KEY")),
            include_checkpoints=True,
            checkpoint_frequency="every_turn",
            max_checkpoints=50,
            context_manager=True,
            cost_tracking=True,
            cost_budget_usd=settings.default_budget_usd,
            context_discovery=True,
            context_manager_max_tokens=body.context_max_tokens,
            skill_directories=["/workspace/skills"],
            thinking="low",
            eviction_token_limit=eviction_limit,
            stuck_loop_detection=body.stuck_loop_detection,
            interrupt_on={"execute": True, "write_file": False},
        )
        deps = DeepAgentDeps(backend=sandbox)

    session = AgentSession(
        hosted_id=hosted_id,
        sandbox=sandbox,
        agent=agent,
        deps=deps,
        api_key=body.api_key,
        heartbeat_seconds=body.heartbeat_seconds,
    )

    # Restore message_history from platform DB (short-term memory)
    if body.message_history:
        session.message_history = body.message_history
        logger.info("Restored {} messages for agent {}", len(body.message_history), hosted_id)

    sessions[hosted_id] = session
    session.start_heartbeat()
    session.start_websocket()  # real-time event channel

    logger.info("Started agent {} with model {}, heartbeat every {}s, ws=enabled", hosted_id, body.model, body.heartbeat_seconds)
    return ActionResponse(status="running", message="Agent started", container_id=hosted_id)


@app.post("/agents/{hosted_id}/stop", response_model=ActionResponse)
async def stop_agent(hosted_id: str):
    """Stop a hosted agent, its container, heartbeat, and WebSocket."""
    session = sessions.pop(hosted_id, None)
    if session:
        session.stop_heartbeat()
        session.stop_websocket()
        try:
            session.sandbox.stop()
        except Exception as e:
            logger.warning("Error stopping sandbox for {}: {}", hosted_id, e)
    logger.info("Stopped agent {}", hosted_id)
    return ActionResponse(status="stopped", message="Agent stopped")


@app.post("/agents/{hosted_id}/chat", response_model=ChatResponse)
async def chat_with_agent(hosted_id: str, body: ChatRequest):
    """Send a message to the hosted agent and get a reply (non-streaming fallback)."""
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(400, "Agent not running. Start it first.")

    if session.chat_lock.locked():
        raise HTTPException(429, "Agent is busy generating a response. Please wait.")

    session.touch()

    async with session.chat_lock:
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
        session.message_history = result.all_messages()[-100:]
        reply, tool_calls, thinking = _extract_response(result)
        return ChatResponse(reply=reply, tool_calls=tool_calls, thinking=thinking)
      except Exception as e:
        logger.error("Chat error for {}: {}", hosted_id, repr(e))
        raise HTTPException(500, f"Agent error: {str(e)}")


@app.post("/agents/{hosted_id}/chat/stream")
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

    if session.chat_lock.locked():
        raise HTTPException(429, "Agent is busy generating a response. Please wait.")

    session.touch()

    async def generate():
      async with session.chat_lock:
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
                                            todos_file = settings.workspace_root / hosted_id / ".deep" / "todos.json"
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
                                all_tool_calls.append({"tool": part.tool_name, "args": args, "status": "done"})

                result = run.result
                session.message_history = result.all_messages()[-100:]

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
                        all_tool_calls.append({"tool": tc.tool_name, "args": args, "status": "done"})
                    logger.info("Auto-approving {} deferred tools ({} blocked)", sum(v for v in approvals.values()), sum(1 for v in approvals.values() if not v))
                    # Mark approved tools as done on the UI
                    for tc in deferred.approvals:
                        if approvals.get(tc.tool_call_id):
                            yield json.dumps({"type": "tool_result", "tool_name": tc.tool_name}) + "\n"
                    result = await session.agent.run(
                        deferred_tool_results=DeferredToolResults(approvals=approvals),
                        deps=session.deps,
                        message_history=result.all_messages(),
                        model_settings={"timeout": settings.chat_timeout},
                    )
                    session.message_history = result.all_messages()[-100:]
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
                session.message_history = result.all_messages()[-100:]

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
                    session.message_history = result.all_messages()[-100:]
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
                    session.message_history = result.all_messages()[-100:]
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

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/agents/{hosted_id}/status", response_model=ActionResponse)
async def agent_status(hosted_id: str):
    """Check if an agent is running. Verifies sandbox is alive."""
    session = sessions.get(hosted_id)
    if not session:
        return ActionResponse(status="stopped")
    # Verify sandbox container is still alive
    try:
        if session.sandbox and hasattr(session.sandbox, "container"):
            container = session.sandbox.container
            if container:
                container.reload()
                if container.status != "running":
                    logger.warning("Sandbox dead for {}, cleaning up", hosted_id)
                    session.stop_heartbeat()
                    session.stop_websocket()
                    sessions.pop(hosted_id, None)
                    return ActionResponse(status="stopped")
    except Exception:
        logger.warning("Sandbox check failed for {}, cleaning up", hosted_id)
        session.stop_heartbeat()
        session.stop_websocket()
        sessions.pop(hosted_id, None)
        return ActionResponse(status="stopped")
    return ActionResponse(status="running")


@app.get("/agents/{hosted_id}/history")
async def get_session_history(hosted_id: str):
    """Get the current message_history for persistence."""
    session = sessions.get(hosted_id)
    if not session:
        return {"history": []}
    try:
        # Serialize pydantic-deepagents message objects to dicts
        serialized = []
        for msg in session.message_history[-30:]:
            if isinstance(msg, dict):
                serialized.append(msg)
            elif hasattr(msg, "model_dump"):
                serialized.append(msg.model_dump(mode="json"))
            elif hasattr(msg, "__dict__"):
                serialized.append(msg.__dict__)
        return {"history": serialized}
    except Exception as e:
        logger.warning("History serialization error for {}: {}", hosted_id, e)
        return {"history": []}


@app.get("/agents/{hosted_id}/checkpoints")
async def list_checkpoints(hosted_id: str):
    """List available checkpoints for rewind."""
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(404, "Agent not running")
    try:
        store = None
        for ts in session.agent.toolsets:
            if hasattr(ts, "checkpoint_store"):
                store = ts.checkpoint_store
                break
        if not store:
            return {"checkpoints": []}
        cps = []
        for cp in store.list():
            cps.append({
                "id": cp.id if hasattr(cp, "id") else str(cp),
                "label": getattr(cp, "label", ""),
                "turn": getattr(cp, "turn", 0),
                "message_count": getattr(cp, "message_count", 0),
                "created_at": getattr(cp, "created_at", ""),
            })
        return {"checkpoints": cps}
    except Exception as e:
        logger.warning("Checkpoint list error for {}: {}", hosted_id, e)
        return {"checkpoints": []}


class RewindRequest(BaseModel):
    checkpoint_id: str


@app.post("/agents/{hosted_id}/rewind")
async def rewind_to_checkpoint(hosted_id: str, body: RewindRequest):
    """Rewind agent to a previous checkpoint."""
    session = sessions.get(hosted_id)
    if not session:
        raise HTTPException(404, "Agent not running")
    try:
        store = None
        for ts in session.agent.toolsets:
            if hasattr(ts, "checkpoint_store"):
                store = ts.checkpoint_store
                break
        if not store:
            raise HTTPException(400, "Checkpoints not available")
        cp = store.get(body.checkpoint_id)
        if not cp:
            raise HTTPException(404, "Checkpoint not found")
        session.message_history = cp.messages if hasattr(cp, "messages") else []
        logger.info("Rewound agent {} to checkpoint {}", hosted_id, body.checkpoint_id)
        return {"status": "ok", "checkpoint_id": body.checkpoint_id, "message_count": len(session.message_history)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Rewind error for {}: {}", hosted_id, e)
        raise HTTPException(500, str(e))


@app.get("/agents/{hosted_id}/todos")
async def get_todos(hosted_id: str):
    """Get agent's current todo list."""
    session = sessions.get(hosted_id)
    if not session:
        return {"todos": []}
    workspace = settings.workspace_root / hosted_id
    todos_file = workspace / ".deep" / "todos.json"
    if todos_file.exists():
        try:
            return {"todos": json.loads(todos_file.read_text())}
        except Exception:
            pass
    return {"todos": []}


@app.get("/agents/{hosted_id}/files")
async def list_workspace_files(hosted_id: str):
    """List all files in the agent's workspace (disk, not DB)."""
    workspace = settings.workspace_root / hosted_id
    if not workspace.exists():
        return {"files": []}

    files = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file() and not path.name.startswith("."):
            rel = str(path.relative_to(workspace))
            try:
                content = path.read_text(encoding="utf-8")
                size = len(content.encode("utf-8"))
            except (UnicodeDecodeError, PermissionError):
                content = None
                size = path.stat().st_size
            files.append({
                "file_path": rel,
                "content": content,
                "size_bytes": size,
            })
    return {"files": files}


@app.delete("/agents/{hosted_id}/files/{file_path:path}")
async def delete_workspace_file(hosted_id: str, file_path: str):
    """Delete a file from the agent's workspace on disk."""
    workspace = settings.workspace_root / hosted_id
    target = workspace / file_path
    if not str(target).startswith(str(workspace)):
        raise HTTPException(403, "Invalid path")
    if target.exists():
        target.unlink()
        return {"status": "deleted"}
    return {"status": "not_found"}


@app.get("/health")
async def health():
    """Health check with active agents info."""
    return {
        "status": "ok",
        "version": "0.3.0",
        "active_agents": len(sessions),
        "max_agents": settings.max_agents,
        "workspace_root": str(settings.workspace_root),
    }


if __name__ == "__main__":
    logger.info("Agent Runner v0.3.0 starting on {}:{}", settings.host, settings.port)
    logger.info("Workspace: {}", settings.workspace_root)
    logger.info("Platform: {}", settings.agentspore_url)
    logger.info("Max agents: {}, idle timeout: {}s", settings.max_agents, settings.idle_timeout_seconds)
    uvicorn.run(app, host=settings.host, port=settings.port)
