"""Append-only execution log recorder.

Record every agent-initiated outbound side effect: provider, operation,
input hash (for idempotency), output ref or error, duration. Pure
observability — no retry/compensation logic here. EE builds saga +
compensation on top of the same table.

Usage::

    async with ExecutionLogger(db).record(
        agent_id=agent_id,
        provider="github",
        operation="issue.create",
        input_payload={"repo": "...", "title": "..."},
    ) as step:
        result = await gh.create_issue(...)
        step.set_output({"id": result["id"]})
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def canonical_input_hash(payload: Any) -> str:
    """SHA-256 of a deterministically-serialised JSON payload. Used as
    an idempotency key together with (agent_id, provider, operation)."""
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ExecutionStep:
    """Mutable handle passed to the caller inside the ``record`` block.
    Set output / resource_id here; the recorder persists them on exit."""

    id: UUID | None = None
    output: dict[str, Any] | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    _extra: dict[str, Any] = field(default_factory=dict)

    def set_output(self, output: dict[str, Any]) -> None:
        self.output = output

    def set_resource(self, resource_type: str, resource_id: str) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id


class ExecutionLogger:
    """Thin recorder around ``execution_log``. Owns a session and commits
    on each row insert / status update so the log is durable even when
    the calling transaction rolls back."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @asynccontextmanager
    async def record(
        self,
        *,
        provider: str,
        operation: str,
        input_payload: Any,
        agent_id: UUID | None = None,
        user_id: UUID | None = None,
        integration_id: UUID | None = None,
        correlation_id: UUID | None = None,
        parent_step_id: UUID | None = None,
        resource_type: str | None = None,
    ):
        step = ExecutionStep(resource_type=resource_type)
        input_hash = canonical_input_hash(input_payload)

        result = await self.db.execute(
            text(
                """
                INSERT INTO execution_log
                    (agent_id, user_id, integration_id, provider, operation,
                     resource_type, correlation_id, parent_step_id,
                     input_hash, input_ref, status, started_at)
                VALUES
                    (:aid, :uid, :iid, :prov, :op,
                     :rt, :cid, :psid,
                     :ih, CAST(:ir AS JSONB), 'pending', now())
                RETURNING id
                """
            ),
            {
                "aid": agent_id,
                "uid": user_id,
                "iid": integration_id,
                "prov": provider,
                "op": operation,
                "rt": resource_type,
                "cid": correlation_id,
                "psid": parent_step_id,
                "ih": input_hash,
                "ir": json.dumps(input_payload, default=str),
            },
        )
        step.id = result.first()[0]
        await self.db.commit()

        started_monotonic = time.monotonic()
        try:
            yield step
        except Exception as exc:
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            await self._finish(
                step.id,
                status="failed",
                duration_ms=duration_ms,
                output=None,
                resource_type=step.resource_type,
                resource_id=step.resource_id,
                error_code=type(exc).__name__[:80],
                error_message=str(exc)[:4000],
            )
            raise
        else:
            duration_ms = int((time.monotonic() - started_monotonic) * 1000)
            await self._finish(
                step.id,
                status="success",
                duration_ms=duration_ms,
                output=step.output,
                resource_type=step.resource_type,
                resource_id=step.resource_id,
                error_code=None,
                error_message=None,
            )

    async def _finish(
        self,
        step_id: UUID,
        *,
        status: str,
        duration_ms: int,
        output: dict[str, Any] | None,
        resource_type: str | None,
        resource_id: str | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        await self.db.execute(
            text(
                """
                UPDATE execution_log
                   SET status = :st,
                       completed_at = now(),
                       duration_ms = :dur,
                       output_ref = CAST(:out AS JSONB),
                       resource_type = COALESCE(:rt, resource_type),
                       resource_id = COALESCE(:rid, resource_id),
                       error_code = :ec,
                       error_message = :em
                 WHERE id = :id
                """
            ),
            {
                "id": step_id,
                "st": status,
                "dur": duration_ms,
                "out": json.dumps(output, default=str) if output is not None else None,
                "rt": resource_type,
                "rid": resource_id,
                "ec": error_code,
                "em": error_message,
            },
        )
        await self.db.commit()
