"""Canonical event envelope and taxonomy.

Event types are dotted namespaces — ``tracker.issue.closed``,
``vcs.push``. Adapters (webhooks, agent calls) map their native shapes
to :class:`Event`; consumers subscribe by exact type or pattern.

OSS-lite scope: taxonomy covers tracker / vcs / agent surfaces. EE
extends with ``workflow.*`` lifecycle events emitted by the workflow
engine.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventSource(str, Enum):
    WEBHOOK = "webhook"
    AGENT = "agent"
    MANUAL = "manual"
    SYSTEM = "system"


CANONICAL_EVENT_TYPES: frozenset[str] = frozenset({
    "tracker.issue.created",
    "tracker.issue.updated",
    "tracker.issue.closed",
    "tracker.issue.reopened",
    "tracker.issue.commented",
    "vcs.push",
    "vcs.pr.opened",
    "vcs.pr.merged",
    "vcs.pr.closed",
    "agent.heartbeat",
    "agent.registered",
})


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID | None = None
    type: str = Field(..., description="Dotted event type, e.g. tracker.issue.closed")
    source_type: EventSource
    source_id: str | None = None
    integration_id: UUID | None = None
    agent_id: UUID | None = None
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
