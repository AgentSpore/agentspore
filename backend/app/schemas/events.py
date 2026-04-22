"""Event bus API schemas."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ManualEvent(BaseModel):
    """Payload for ``POST /api/v1/events`` — agent publishes a synthetic
    event into the bus. Scoped to caller's agent_id for audit."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    integration_id: UUID | None = None
    correlation_id: UUID | None = None


def event_row_to_dict(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "source_type": row["source_type"],
        "source_id": row.get("source_id"),
        "integration_id": str(row["integration_id"]) if row.get("integration_id") else None,
        "agent_id": str(row["agent_id"]) if row.get("agent_id") else None,
        "correlation_id": str(row["correlation_id"]) if row.get("correlation_id") else None,
        "payload": row.get("payload") or {},
        "status": row["status"],
        "occurred_at": row.get("occurred_at"),
    }
