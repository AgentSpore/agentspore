"""Repository for replay_cases — prod-trace samples for offline eval."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.replay_case import ReplayCaseCreate, ReplayCaseResponse


class ReplayCaseRepository:
    """CRUD for replay_cases table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, payload: ReplayCaseCreate) -> ReplayCaseResponse:
        """Insert one replay case and return the created row."""
        result = await self.db.execute(
            text("""
                INSERT INTO replay_cases
                    (hosted_agent_id, agent_handle, model, trace_id,
                     input_messages, output_text, tool_calls, duration_ms,
                     status, metadata)
                VALUES
                    (:hosted_agent_id, :agent_handle, :model, :trace_id,
                     :input_messages, :output_text, :tool_calls, :duration_ms,
                     :status, :metadata)
                RETURNING *
            """),
            {
                "hosted_agent_id": str(payload.hosted_agent_id),
                "agent_handle": payload.agent_handle,
                "model": payload.model,
                "trace_id": payload.trace_id,
                "input_messages": json.dumps(payload.input_messages),
                "output_text": payload.output_text,
                "tool_calls": json.dumps(payload.tool_calls),
                "duration_ms": payload.duration_ms,
                "status": payload.status,
                "metadata": json.dumps(payload.metadata),
            },
        )
        await self.db.commit()
        row = result.mappings().one()
        return ReplayCaseResponse(**dict(row))

    async def list_by_agent(
        self,
        *,
        agent_handle: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReplayCaseResponse]:
        """List replay cases, optionally filtered by agent_handle."""
        if agent_handle:
            result = await self.db.execute(
                text("""
                    SELECT * FROM replay_cases
                    WHERE agent_handle = :agent_handle
                    ORDER BY captured_at DESC
                    LIMIT :limit OFFSET :offset
                """),
                {"agent_handle": agent_handle, "limit": limit, "offset": offset},
            )
        else:
            result = await self.db.execute(
                text("""
                    SELECT * FROM replay_cases
                    ORDER BY captured_at DESC
                    LIMIT :limit OFFSET :offset
                """),
                {"limit": limit, "offset": offset},
            )
        rows = result.mappings().all()
        return [ReplayCaseResponse(**dict(r)) for r in rows]


def get_replay_case_repo(db: AsyncSession = Depends(get_db)) -> ReplayCaseRepository:
    """FastAPI Depends factory."""
    return ReplayCaseRepository(db)
