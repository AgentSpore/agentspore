"""Internal endpoint for prod-trace replay case ingestion and retrieval.

Access is restricted to agent-runner via X-Runner-Key header.
No user auth — these calls originate from the runner infra, not browsers.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from loguru import logger

from app.core.config import Settings, get_settings
from app.repositories.replay_case_repo import ReplayCaseRepository, get_replay_case_repo
from app.schemas.replay_case import ReplayCaseCreate, ReplayCaseResponse
from app.services.replay_case_service import ReplayCaseService

router = APIRouter(prefix="/internal", tags=["internal"])


def _require_runner_key(
    x_runner_key: str = Header(default="", alias="X-Runner-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Dependency: enforce X-Runner-Key header against settings.agent_runner_key."""
    if not settings.agent_runner_key:
        raise HTTPException(403, "Runner key not configured on server")
    if not x_runner_key or not secrets.compare_digest(x_runner_key, settings.agent_runner_key):
        raise HTTPException(403, "Unauthorized")


def _get_service(repo: ReplayCaseRepository = Depends(get_replay_case_repo)) -> ReplayCaseService:
    return ReplayCaseService(repo)


@router.post(
    "/replay-cases",
    response_model=ReplayCaseResponse,
    status_code=201,
    dependencies=[Depends(_require_runner_key)],
)
async def create_replay_case(
    payload: ReplayCaseCreate,
    svc: ReplayCaseService = Depends(_get_service),
) -> ReplayCaseResponse:
    """Ingest one sampled prod trace. Called fire-and-forget by agent-runner."""
    logger.debug(
        "replay_case ingest: agent={} status={} trace={}",
        payload.agent_handle,
        payload.status,
        payload.trace_id,
    )
    return await svc.ingest(payload)


@router.get(
    "/replay-cases",
    response_model=list[ReplayCaseResponse],
    dependencies=[Depends(_require_runner_key)],
)
async def list_replay_cases(
    agent_handle: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: ReplayCaseService = Depends(_get_service),
) -> list[ReplayCaseResponse]:
    """List sampled replay cases for inspection / offline eval."""
    return await svc.list_cases(agent_handle=agent_handle, limit=limit, offset=offset)
