"""Health check endpoint."""

from fastapi import APIRouter

from config import get_settings
from session import sessions

settings = get_settings()

router = APIRouter()


@router.get("/health")
async def health():
    """Health check with active agents info."""
    return {
        "status": "ok",
        "version": "0.3.0",
        "active_agents": len(sessions),
        "max_agents": settings.max_agents,
        "workspace_root": str(settings.workspace_root),
    }
