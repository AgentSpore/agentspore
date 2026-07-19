"""Agent lifecycle endpoints: start, stop, status."""

import os
from pathlib import PurePosixPath

import httpx
from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic_ai import DeferredToolRequests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai_backends import RuntimeConfig

from config import get_settings
from llm_fallback import resolve_model_for_agent
from pydantic_deep import DeepAgent, DeepAgentDeps, create_deep_agent
from sandbox import SecureDockerSandbox
from schemas import ActionResponse, StartRequest
from session import AgentSession, sanitize_history, sessions
from tools.search_past_runs import make_search_past_runs_tool
from workspace import _init_workspace_git

settings = get_settings()

router = APIRouter()


@router.post("/agents/{hosted_id}/start", response_model=ActionResponse)
async def start_agent(hosted_id: str, body: StartRequest):
    """Start a hosted agent in a Docker container with its own heartbeat."""
    if hosted_id in sessions:
        raise HTTPException(400, "Agent already running")

    if len(sessions) >= settings.max_agents:
        raise HTTPException(503, f"Max {settings.max_agents} agents reached")

    workspace = settings.workspace_root / hosted_id
    workspace.mkdir(parents=True, exist_ok=True)

    for subdir in ["memory", "checkpoints", "plans", "skills"]:
        (workspace / subdir).mkdir(parents=True, exist_ok=True)

    for f in body.files:
        fp = f.get("file_path", "")
        if not fp:
            continue
        # Skip virtual-env and bytecode dirs — overwriting an in-use
        # interpreter binary raises OSError ETXTBSY (errno 26).
        _skip_parts = {"venv", ".venv", "__pycache__", "node_modules"}
        if _skip_parts.intersection(PurePosixPath(fp).parts):
            continue
        file_path = workspace / fp
        if not str(file_path).startswith(str(workspace)):
            continue
        # No-clobber: skip files that already exist on the persistent workspace.
        # Payload carries config seed files only; existing working files (scripts,
        # data, agent edits) must survive a restart without being overwritten.
        if file_path.exists():
            logger.debug("Skipping existing workspace file on restart: {}", fp)
            continue
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f.get("content", "") or "", encoding="utf-8")

    # AGENT.md = pure system_prompt. Platform credentials reach the sandbox
    # as env vars (AGENTSPORE_AGENT_ID/API_KEY/PLATFORM_URL) injected via
    # RuntimeConfig below; doc lives in SKILL.md (Authentication section).
    (workspace / "AGENT.md").write_text(body.system_prompt, encoding="utf-8")

    memory_file = workspace / ".deep" / "memory" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    if not memory_file.exists():
        memory_file.write_text("", encoding="utf-8")

    # Always refresh .deep/skills/SKILL.md from platform so agents see latest
    # endpoints and auth docs. SkillsToolset auto-discovers files under
    # /workspace/.deep/skills/. Falls back silently to whatever is on disk.
    skill_file = workspace / ".deep" / "skills" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_skill = workspace / "SKILL.md"
    if legacy_skill.exists():
        try:
            legacy_skill.unlink()
        except OSError:
            pass
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{settings.agentspore_url}/skill.md")
            if r.status_code == 200:
                skill_file.write_text(r.text, encoding="utf-8")
                logger.info("Refreshed .deep/skills/SKILL.md for agent {}", hosted_id)
    except Exception as e:
        logger.warning("Could not fetch SKILL.md: {}", e)

    _init_workspace_git(workspace)

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

    sandbox_runtime = RuntimeConfig(
        name="agentspore-env",
        image=settings.docker_image,
        env_vars={
            "AGENTSPORE_AGENT_ID": body.agent_id,
            "AGENTSPORE_API_KEY": body.api_key,
            "AGENTSPORE_PLATFORM_URL": settings.agentspore_url,
        },
    )
    sandbox = SecureDockerSandbox(
        image=settings.docker_image,
        work_dir="/workspace",
        volumes={str(workspace): "/workspace"},
        auto_remove=True,
        runtime=sandbox_runtime,
    )

    # Eviction limit = ~5% of model context window (min 5K)
    # Large outputs beyond this limit are automatically truncated
    ctx = body.context_max_tokens or 128000
    eviction_limit = max(5000, ctx // 20)

    # Pydantic-deep 0.3.16+: instructions REPLACES base prompt instead of
    # appending. To preserve the framework's tool-usage guidance, only pass
    # `instructions` when the owner actually provided a custom system prompt.
    # An empty/blank value falls through to the library's default DeepAgent
    # base prompt.
    custom_instructions = (body.system_prompt or "").strip() or None

    # Resolve model through fallback chain — guards against stale / removed model IDs.
    resolved_model = resolve_model_for_agent(body.model)

    # Strip provider prefix for API call (e.g. "cerebras/llama-3.3-70b" → "llama-3.3-70b").
    # OpenRouter models keep the full ID including :free suffix.
    api_model = resolved_model
    if "/" in resolved_model and not resolved_model.endswith(":free"):
        api_model = resolved_model.split("/", 1)[1]

    effective_base_url = body.provider_base_url or settings.openai_base_url
    effective_api_key = body.provider_api_key or settings.openai_api_key

    openai_provider = OpenAIProvider(base_url=effective_base_url, api_key=effective_api_key)
    model_obj = OpenAIChatModel(api_model, provider=openai_provider)

    # Bind search_past_runs to this agent's handle so the LLM cannot spoof
    # cross-agent history queries (handle is captured in closure, not arg).
    # Available to every hosted agent regardless of yaml / programmatic path.
    runner_extra_tools = [make_search_past_runs_tool(body.agent_handle)]

    # Load agent from workspace agent.yaml if exists, otherwise use defaults
    agent_yaml = workspace / "agent.yaml"
    if agent_yaml.exists():
        logger.info("Loading agent spec from {}", agent_yaml)
        from_file_kwargs = dict(
            model=model_obj,
            backend=sandbox,
            output_type=[str, DeferredToolRequests],
            tools=runner_extra_tools,
            cost_budget_usd=settings.default_budget_usd,
            context_manager_max_tokens=body.context_max_tokens,
            eviction_token_limit=eviction_limit,
            # Force per-turn checkpointing regardless of what the on-disk
            # agent.yaml declares. The pydantic-deep default is "every_tool",
            # which means a chat with no tool calls leaves the checkpoint
            # store empty and the owner's Rewind dropdown shows nothing.
            # Older auto-generated agent.yaml files don't carry an explicit
            # frequency, so we pin it here to keep Rewind useful for every
            # hosted agent.
            checkpoint_frequency="every_turn",
            max_checkpoints=50,
            # include_liteparse is a create_deep_agent kwarg, not a DeepAgentSpec
            # field. Pass it via overrides so DeepAgent.from_file routes it to
            # passthrough kwargs without tripping DeepAgentSpec(extra='forbid').
            include_liteparse=True,
            # SkillsToolset runs in the runner process and reads files
            # directly from the host FS, so /workspace paths from agent.yaml
            # must be remapped to the actual on-host workspace directory
            # before discovery. MemoryToolset, in contrast, writes through
            # the sandbox backend and needs the sandbox-side /workspace path,
            # so we leave memory_dir alone.
            skill_directories=[str(workspace / ".deep" / "skills")],
        )
        if custom_instructions:
            from_file_kwargs["instructions"] = custom_instructions
        agent, deps = DeepAgent.from_file(str(agent_yaml), **from_file_kwargs)
    else:
        agent = create_deep_agent(
            model=model_obj,
            instructions=custom_instructions,
            output_type=[str, DeferredToolRequests],
            tools=runner_extra_tools,
            include_todo=True,
            include_filesystem=True,
            include_execute=True,
            include_liteparse=True,
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
            skill_directories=["/workspace/.deep/skills"],
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
        agent_handle=body.agent_handle,
        model=resolved_model,
        max_concurrent_sessions=body.max_concurrent_sessions,
    )

    # Restore message_history from platform DB (short-term memory).
    # Trim trailing dicts that look like ModelResponse with orphan
    # ToolCallParts — legacy rows saved before sanitize_history was in
    # place can otherwise trigger "Tool call was cancelled." injection.
    if body.message_history:
        history = sanitize_history(list(body.message_history))
        session.message_history = history
        trimmed = len(body.message_history) - len(history)
        logger.info("Restored {} messages for agent {} (trimmed {} orphan trailing)", len(history), hosted_id, trimmed)

    sessions[hosted_id] = session
    session.start_heartbeat()
    session.start_websocket()  # real-time event channel
    session.start_quota_watcher()  # disk quota enforcement

    logger.info("Started agent {} with model {} (resolved: {}), heartbeat every {}s, ws=enabled", hosted_id, body.model, resolved_model, body.heartbeat_seconds)
    return ActionResponse(status="running", message="Agent started", container_id=hosted_id)


@router.post("/agents/{hosted_id}/stop", response_model=ActionResponse)
async def stop_agent(hosted_id: str):
    """Stop a hosted agent, its container, heartbeat, and WebSocket."""
    session = sessions.pop(hosted_id, None)
    if session:
        session.stop_heartbeat()
        session.stop_websocket()
        session.stop_quota_watcher()
        try:
            session.sandbox.stop()
        except Exception as e:
            logger.warning("Error stopping sandbox for {}: {}", hosted_id, e)
    logger.info("Stopped agent {}", hosted_id)
    return ActionResponse(status="stopped", message="Agent stopped")


@router.get("/agents/{hosted_id}/status")
async def agent_status(hosted_id: str):
    """Check if an agent is running.

    Returns extended status including ``busy``, ``busy_session_id``,
    ``startup_done``, ``worker_pool``, and ``sessions`` fields.

    Response schema:
      {
        "status": "running" | "stopped",
        "busy": bool,
        "busy_session_id": str | null,   # owner_session_id that holds chat_lock
        "startup_done": bool,            # False while bootstrap LLM call is in flight
        "worker_pool": {
          "total": int,                  # max_concurrent_sessions
          "busy": int,                   # currently executing
          "available": int               # free slots
        },
        "sessions": [
          {
            "session_id": str,
            "status": "running" | "queued" | "idle",
            "queue_depth": int,
            "last_active": str           # ISO-8601 UTC timestamp
          },
          ...
        ]
      }

    Backward-compat: all pre-Phase-2 fields are preserved; new fields are additive.
    """
    session = sessions.get(hosted_id)
    if not session:
        return {
            "status": "stopped",
            "busy": False,
            "busy_session_id": None,
            "startup_done": True,
            "worker_pool": {"total": 1, "busy": 0, "available": 1},
            "sessions": [],
        }
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
                    return {
                        "status": "stopped",
                        "busy": False,
                        "busy_session_id": None,
                        "startup_done": True,
                        "worker_pool": {"total": 1, "busy": 0, "available": 1},
                        "sessions": [],
                    }
    except Exception:
        logger.warning("Sandbox check failed for {}, cleaning up", hosted_id)
        session.stop_heartbeat()
        session.stop_websocket()
        sessions.pop(hosted_id, None)
        return {
            "status": "stopped",
            "busy": False,
            "busy_session_id": None,
            "startup_done": True,
            "worker_pool": {"total": 1, "busy": 0, "available": 1},
            "sessions": [],
        }

    is_busy = session.chat_lock.locked()
    pool_snapshot = session.worker_pool.status_snapshot()
    return {
        "status": "running",
        "busy": is_busy,
        "busy_session_id": session.active_session_id if is_busy else None,
        "startup_done": session.bootstrap_done,
        **pool_snapshot,
    }
