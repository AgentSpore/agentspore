"""Pydantic v2 schemas for replay_cases (prod-trace sampling for offline eval)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReplayCaseCreate(BaseModel):
    """Payload sent by agent-runner when sampling a completed chat run."""

    hosted_agent_id: UUID
    agent_handle: str
    model: str
    trace_id: str | None = None
    input_messages: list[dict[str, Any]]
    output_text: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int | None = None
    status: str  # 'completed' | 'failed' | 'truncated'
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplayCaseResponse(BaseModel):
    """Row returned from the DB."""

    id: UUID
    captured_at: datetime
    hosted_agent_id: UUID
    agent_handle: str
    model: str
    trace_id: str | None
    input_messages: list[dict[str, Any]]
    output_text: str | None
    tool_calls: list[dict[str, Any]]
    duration_ms: int | None
    status: str
    metadata: dict[str, Any]
