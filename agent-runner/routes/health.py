"""Health check endpoint."""

import asyncio

import anyio
import docker
from fastapi import APIRouter

from config import get_settings
from session import sessions

settings = get_settings()

router = APIRouter()


def _ping_docker_daemon() -> None:
    """Ping the Docker daemon (blocking). Raises on any failure.

    ``timeout=3`` bounds the underlying HTTP socket read so this worker thread
    always returns within ~3s even against a wedged-but-listening daemon. This
    is what prevents the thread-pool leak the reviewer flagged: cancelling the
    asyncio.wait_for offload does NOT kill the thread, so the blocking call must
    be incapable of hanging on its own.
    """
    docker.from_env(timeout=3).ping()


async def _docker_daemon_status() -> str:
    """Probe the Docker daemon, never raising out of the handler.

    Returns ``"ok"`` when the daemon answers a ping, otherwise
    ``"error: <short reason>"`` (reason truncated to ~120 chars). The blocking
    SDK call runs in a worker thread so the event loop is never blocked, and a
    hung daemon is bounded by a 5-second timeout (itself a useful signal).
    """
    try:
        await asyncio.wait_for(anyio.to_thread.run_sync(_ping_docker_daemon), timeout=5)
    except asyncio.TimeoutError:
        return "error: ping timeout"
    except Exception as exc:  # noqa: BLE001 — surface any daemon failure as a field, never raise
        return "error: {}".format(str(exc)[:120])
    return "ok"


@router.get("/health")
async def health():
    """Health check with active agents info and Docker daemon liveness."""
    return {
        "status": "ok",
        "version": "0.3.0",
        "active_agents": len(sessions),
        "max_agents": settings.max_agents,
        "workspace_root": str(settings.workspace_root),
        "docker_daemon": await _docker_daemon_status(),
    }
