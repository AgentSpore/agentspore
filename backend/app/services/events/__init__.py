"""Event bus for agent choreography (OSS-lite).

Canonical taxonomy + durable append-only log + Redis live fanout.
Publishers emit via :class:`EventPublisher`; live consumers tail via
``GET /api/v1/events/stream`` (SSE). EE adds subscriptions + workflow
dispatcher on top of the same table.
"""

from __future__ import annotations

from .publisher import REDIS_CHANNEL_PREFIX, EventPublisher, safe_publish
from .schema import CANONICAL_EVENT_TYPES, Event, EventSource

__all__ = [
    "CANONICAL_EVENT_TYPES",
    "Event",
    "EventPublisher",
    "EventSource",
    "REDIS_CHANNEL_PREFIX",
    "safe_publish",
]
