"""Agent Runner Service — manages pydantic-deepagents in Docker containers.

Runs on the infra server (178.154.244.194). The main AgentSpore backend
communicates with this service to create, start, stop, and chat with
hosted agents. Each agent runs in an isolated Docker container with
its own heartbeat schedule.

v0.3.0 — streaming chat, include_execute, idle cleanup
"""

import asyncio
import atexit
import os
import secrets
import signal
import sys

import docker  # noqa: F401 — re-exported so test patches work as `main.docker.from_env`
import httpx
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from loguru import logger
from starlette.responses import JSONResponse

from config import get_settings
from llm_fallback import resolve_model_for_agent  # noqa: F401 — kept for API compat
from quota import DiskQuotaManager

# ── Re-exports for test compatibility ────────────────────────────────────────
# tests/test_sandbox_security.py:  from main import SecureDockerSandbox
#                                  from main import is_command_safe
# tests/test_sanitize_history.py:  from main import sanitize_history
# tests/test_history_integration.py: from main import sanitize_history
# tests/test_checkpoints.py:       import main  (uses main.app, main.sessions)
import session as _session_mod
import routes.admin as _admin_mod
import routes.files as _files_mod
from sandbox import BLOCKED_COMMANDS, SecureDockerSandbox, is_command_safe  # noqa: F401
from session import (
    AgentSession,  # noqa: F401
    cleanup_all_sessions,
    idle_cleanup_loop,
    sanitize_history,
    sessions,
)
from schemas import (  # noqa: F401
    ActionResponse,
    ChatRequest,
    ChatResponse,
    RewindRequest,
    StartRequest,
    WriteFileRequest,
)
from workspace import _init_workspace_git, _safe_workspace_path  # noqa: F401
from routes.agents import router as agents_router, start_agent  # noqa: F401
from routes.chat import router as chat_router
from routes.history import router as history_router, _resolve_checkpoint_store  # noqa: F401
from routes.files import router as files_router
from routes.admin import router as admin_router
from routes.health import router as health_router

# ── Settings & disk quota ─────────────────────────────────────────────────────

settings = get_settings()

disk_quota = DiskQuotaManager(
    workspace_root=settings.workspace_root,
    soft_mb=settings.agent_disk_soft_mb,
    hard_mb=settings.agent_disk_hard_mb,
    enabled=settings.agent_disk_quota_enabled,
    agentspore_url=settings.agentspore_url,
    runner_key=settings.runner_key,
)

# Expose LLM credentials as env vars for pydantic-ai / openai client
if settings.openai_api_key:
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
if settings.openai_base_url:
    os.environ.setdefault("OPENAI_BASE_URL", settings.openai_base_url)
if settings.docker_host:
    os.environ.setdefault("DOCKER_HOST", settings.docker_host)

# Wire disk_quota into submodules that need it. These modules cannot import
# DiskQuotaManager themselves at module level without creating a circular
# dependency through quota → main; instead they receive the instance here
# after it is constructed.
_session_mod.disk_quota = disk_quota
_admin_mod.disk_quota = disk_quota
_files_mod.disk_quota = disk_quota

# ── Startup helpers ───────────────────────────────────────────────────────────


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


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Agent Runner", version="0.3.0", lifespan=lifespan)


@app.middleware("http")
async def verify_runner_key(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    # runner_key is required (no default) — startup would have failed if unset.
    key = request.headers.get("X-Runner-Key", "")
    if not key or not secrets.compare_digest(key, settings.runner_key):
        return JSONResponse({"detail": "Unauthorized"}, status_code=403)
    return await call_next(request)


signal.signal(signal.SIGTERM, lambda *_: (cleanup_all_sessions(), sys.exit(0)))
atexit.register(cleanup_all_sessions)

# ── Router includes ───────────────────────────────────────────────────────────

app.include_router(agents_router)
app.include_router(chat_router)
app.include_router(history_router)
app.include_router(files_router)
app.include_router(admin_router)
app.include_router(health_router)

# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Agent Runner v0.3.0 starting on {}:{}", settings.host, settings.port)
    logger.info("Workspace: {}", settings.workspace_root)
    logger.info("Platform: {}", settings.agentspore_url)
    logger.info("Max agents: {}, idle timeout: {}s", settings.max_agents, settings.idle_timeout_seconds)
    uvicorn.run(app, host=settings.host, port=settings.port)
