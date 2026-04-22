"""Read-only execution log query for agents.

Agents inspect their own outbound call history for debugging, auditing,
and idempotency lookup. Writes happen implicitly via the
``ExecutionLogger`` recorder used inside service code.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.agent_service import get_agent_by_api_key

router = APIRouter(prefix="/execution-log", tags=["execution-log"])


def _row_to_dict(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "agent_id": str(row["agent_id"]) if row.get("agent_id") else None,
        "provider": row["provider"],
        "operation": row["operation"],
        "resource_type": row.get("resource_type"),
        "resource_id": row.get("resource_id"),
        "correlation_id": str(row["correlation_id"]) if row.get("correlation_id") else None,
        "status": row["status"],
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "duration_ms": row.get("duration_ms"),
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
    }


@router.get("")
async def list_execution_log(
    agent: Annotated[dict, Depends(get_agent_by_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: str | None = Query(None),
    status: str | None = Query(None, description="pending|success|failed"),
    operation: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    sql = "SELECT * FROM execution_log WHERE agent_id = :aid"
    params: dict[str, Any] = {"aid": agent["id"], "lim": limit}
    if provider:
        sql += " AND provider = :prov"
        params["prov"] = provider
    if status:
        sql += " AND status = :st"
        params["st"] = status
    if operation:
        sql += " AND operation = :op"
        params["op"] = operation
    sql += " ORDER BY started_at DESC LIMIT :lim"
    result = await db.execute(text(sql), params)
    return [_row_to_dict(dict(r)) for r in result.mappings()]


@router.get("/{step_id}")
async def get_execution_step(
    step_id: UUID,
    agent: Annotated[dict, Depends(get_agent_by_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        text("SELECT * FROM execution_log WHERE id = :id AND agent_id = :aid"),
        {"id": step_id, "aid": agent["id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="execution step not found")
    out = _row_to_dict(dict(row))
    out["input_ref"] = row.get("input_ref") or {}
    out["output_ref"] = row.get("output_ref")
    return out
