"""AgentEventRepository — data access for the durable agent_events outbox (V65).

Answers "did this event reach THIS agent, and did the agent confirm it".
Distinct from the ``events`` bus (V50), which is an append-only audit log —
see V65__agent_events.sql for the rationale.

Every transition here is idempotent and scoped to the target agent: an ack
from the wrong agent, or a second ack for the same event, must never mutate
a row.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

# Statuses of an event that is still live: not yet acknowledged, not expired.
LIVE_STATUSES: tuple[str, ...] = ("pending", "delivered", "queued")

_ACK_STMT = text(
    """
    UPDATE agent_events
    SET status = 'acked',
        acked_at = NOW(),
        dispatched_at = COALESCE(dispatched_at, NOW())
    WHERE event_id = ANY(:event_ids)
      AND target_agent_id = :agent_id
      AND acked_at IS NULL
    RETURNING event_id
    """
).bindparams(
    bindparam("event_ids", type_=ARRAY(PGUUID(as_uuid=True))),
    bindparam("agent_id", type_=PGUUID(as_uuid=True)),
)


class AgentEventRepository:
    """All database operations for the durable agent_events outbox."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        target_agent_id: str,
        event_type: str,
        payload: dict[str, Any],
        ttl_seconds: int,
    ) -> str:
        """Insert a pending outbox row and return its event_id.

        Does not commit — the caller owns the transaction boundary so the
        row is durable before any transport is attempted (outbox discipline,
        mirroring EventPublisher.publish_and_commit).
        """
        result = await self.db.execute(
            text(
                """
                INSERT INTO agent_events
                    (target_agent_id, type, payload, status, expires_at)
                VALUES
                    (CAST(:agent_id AS UUID), :type, CAST(:payload AS JSONB), 'pending',
                     NOW() + make_interval(secs => :ttl))
                RETURNING event_id
                """
            ),
            {
                "agent_id": str(target_agent_id),
                "type": event_type,
                "payload": json.dumps(payload or {}, default=str),
                "ttl": ttl_seconds,
            },
        )
        return str(result.scalar_one())

    async def mark_dispatched(self, event_id: str, status: str) -> None:
        """Record a transport attempt. ``status`` is 'delivered' or 'queued'.

        'delivered' means a live receiver was confirmed; 'queued' means the
        transport found nobody home and the heartbeat drain owns it now.
        Never downgrades an already-acked row.
        """
        await self.db.execute(
            text(
                """
                UPDATE agent_events
                SET status = :status,
                    dispatched_at = NOW(),
                    attempt_count = attempt_count + 1
                WHERE event_id = CAST(:event_id AS UUID)
                  AND acked_at IS NULL
                """
            ),
            {"status": status, "event_id": str(event_id)},
        )

    async def list_unacked(self, target_agent_id: str) -> list[dict]:
        """Return every live, unexpired event for this agent (heartbeat drain).

        Scoped to the target agent, so one agent can never observe another's
        events. At-least-once by design: rows keep coming back until acked.
        """
        result = await self.db.execute(
            text(
                """
                SELECT event_id, type, payload, status, attempt_count,
                       created_at, dispatched_at, expires_at
                FROM agent_events
                WHERE target_agent_id = CAST(:agent_id AS UUID)
                  AND acked_at IS NULL
                  AND status <> 'expired'
                  AND expires_at > NOW()
                ORDER BY created_at
                """
            ),
            {"agent_id": str(target_agent_id)},
        )
        return [dict(row) for row in result.mappings()]

    async def mark_drained(self, event_ids: list[str]) -> None:
        """Mark events as handed to the agent in a heartbeat response body.

        The heartbeat response is a confirmed handoff, so status becomes
        'delivered' — but only an ack proves the agent processed it.
        """
        if not event_ids:
            return
        await self.db.execute(
            text(
                """
                UPDATE agent_events
                SET status = 'delivered',
                    dispatched_at = NOW(),
                    attempt_count = attempt_count + 1
                WHERE event_id = ANY(:event_ids)
                  AND acked_at IS NULL
                """
            ).bindparams(bindparam("event_ids", type_=ARRAY(PGUUID(as_uuid=True)))),
            {"event_ids": [UUID(str(e)) for e in event_ids]},
        )

    async def mark_acked(self, target_agent_id: str, event_ids: list[str]) -> list[str]:
        """Acknowledge events on behalf of ``target_agent_id``.

        Returns the ids actually transitioned. Only the target agent can ack
        (``target_agent_id`` guard), and a repeat ack is a no-op rather than
        an error (``acked_at IS NULL`` guard) — so acked_at never moves once
        set. Both properties are load-bearing: the ack is the platform's only
        truthful liveness signal.
        """
        if not event_ids:
            return []
        try:
            ids = [UUID(str(e)) for e in event_ids]
            agent_uuid = UUID(str(target_agent_id))
        except (ValueError, AttributeError, TypeError):
            return []  # malformed ids from an agent are not an error, just no-op
        result = await self.db.execute(
            _ACK_STMT, {"event_ids": ids, "agent_id": agent_uuid}
        )
        return [str(row[0]) for row in result.fetchall()]
