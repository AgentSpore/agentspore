"""Admin API — operational endpoints for platform operators.

All routes require is_admin=True. Authentication is via the standard
JWT bearer token used by regular endpoints (same get_admin_user dep).
"""

import asyncio
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_user, DatabaseSession
from app.core.config import get_settings
from app.models import User

router = APIRouter(prefix="/admin", tags=["admin"])

AdminUser = Annotated[User, Depends(get_admin_user)]


# ── Schemas ───────────────────────────────────────────────────────────────────


class AgentDiskEntry(BaseModel):
    """Disk usage for a single hosted agent workspace directory."""

    hosted_id: str
    agent_name: str | None
    owner_user_id: str
    status: str
    disk_usage: str | None  # human-readable (e.g. "12M"), None if dir missing or runner error


class DiskUsageResponse(BaseModel):
    """Aggregated disk usage across all hosted agents returned by the runner."""

    agents: list[AgentDiskEntry]
    total_dirs: int
    runner_reachable: bool
    error: str | None = None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get(
    "/hosted-agents/disk-usage",
    response_model=DiskUsageResponse,
    summary="Per-agent disk usage on the runner host",
    description=(
        "Queries the Agent Runner for disk usage of each "
        "/data/agents/<uuid> workspace. Falls back gracefully if the "
        "runner is unreachable — reports runner_reachable=false and "
        "null disk_usage for all agents. Useful for pre-hackathon capacity checks."
    ),
)
async def get_hosted_agents_disk_usage(
    _admin: AdminUser,
    db: DatabaseSession,
) -> DiskUsageResponse:
    """Return disk usage per hosted agent workspace from the runner.

    Strategy:
    1. Query DB for all hosted_agents rows (id, owner, status, agent_name).
    2. Call runner GET /admin/disk-usage (returns {hosted_id: "12M", ...}).
       If runner is unreachable, disk_usage is set to None for every agent.
    3. Merge and return.

    The runner endpoint is expected to respond with:
        {"usage": {"<hosted_id>": "12M", "<hosted_id2>": "4.5M", ...}}

    If the runner does not yet have this endpoint (older deploy) the
    handler degrades gracefully: runner_reachable=False, all disk_usage=None.
    """
    settings = get_settings()
    runner_url = settings.agent_runner_url

    # 1. Fetch all hosted agents from DB
    result = await db.execute(text("""
        SELECT
            h.id::text AS hosted_id,
            h.owner_user_id::text AS owner_user_id,
            h.status,
            a.name AS agent_name
        FROM hosted_agents h
        LEFT JOIN agents a ON a.id = h.agent_id
        ORDER BY h.created_at DESC
    """))
    rows = [dict(r) for r in result.mappings()]

    # 2. Query runner for disk usage (best-effort)
    usage_map: dict[str, str] = {}
    runner_reachable = False
    runner_error: str | None = None

    if runner_url:
        headers = {}
        if settings.agent_runner_key:
            headers["X-Runner-Key"] = settings.agent_runner_key
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{runner_url}/admin/disk-usage", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    usage_map = data.get("usage", {})
                    runner_reachable = True
                elif resp.status_code == 404:
                    # Runner too old — endpoint not deployed yet
                    runner_error = "Runner does not support /admin/disk-usage (upgrade required)"
                    logger.warning("Runner /admin/disk-usage returned 404 — endpoint not implemented")
                else:
                    runner_error = f"Runner returned {resp.status_code}"
                    logger.warning("Runner disk-usage error: {}", resp.status_code)
        except Exception as exc:
            runner_error = f"Runner unreachable: {exc!r}"
            logger.warning("Runner disk-usage request failed: {}", exc)
    else:
        runner_error = "AGENT_RUNNER_URL not configured"

    # 3. Merge
    entries = [
        AgentDiskEntry(
            hosted_id=row["hosted_id"],
            agent_name=row.get("agent_name"),
            owner_user_id=row["owner_user_id"],
            status=row["status"],
            disk_usage=usage_map.get(row["hosted_id"]),
        )
        for row in rows
    ]

    return DiskUsageResponse(
        agents=entries,
        total_dirs=len(entries),
        runner_reachable=runner_reachable,
        error=runner_error,
    )
