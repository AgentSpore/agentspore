"""Repository for replay_cases — prod-trace samples for offline eval."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.replay_case import ReplayCaseCreate, ReplayCaseResponse, ReplayCaseSummary


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


    async def search(
        self,
        *,
        q: str,
        agent_handle: str | None = None,
        status: str | None = None,
        limit: int = 5,
    ) -> list[ReplayCaseSummary]:
        """ILIKE-based MVP search across output_text + input_messages JSONB.

        Returns lightweight summaries (input snippet, tool_calls_count) to
        avoid response bloat. Postgres tsvector / pgvector embedding search
        is deferred to phase 2.
        """
        pattern = f"%{q}%"
        params: dict[str, object] = {
            "pattern": pattern,
            "limit": max(1, min(limit, 20)),
        }

        where_clauses = [
            "(output_text ILIKE :pattern OR input_messages::text ILIKE :pattern)",
        ]
        if agent_handle:
            where_clauses.append("agent_handle = :agent_handle")
            params["agent_handle"] = agent_handle
        if status:
            where_clauses.append("status = :status")
            params["status"] = status

        sql = f"""
            SELECT id, captured_at, agent_handle, model, status,
                   input_messages, output_text, tool_calls, duration_ms
            FROM replay_cases
            WHERE {' AND '.join(where_clauses)}
            ORDER BY captured_at DESC
            LIMIT :limit
        """
        result = await self.db.execute(text(sql), params)
        rows = result.mappings().all()
        return [self._to_summary(dict(r)) for r in rows]

    @staticmethod
    def _to_summary(row: dict[str, object]) -> ReplayCaseSummary:
        """Map a SELECT row to ReplayCaseSummary, snipping input to 200 chars."""
        input_messages = row.get("input_messages") or []
        # input_messages may be returned as list[dict] (JSONB auto-decoded) or
        # str (raw JSON) depending on driver — handle both.
        if isinstance(input_messages, str):
            try:
                input_messages = json.loads(input_messages)
            except (ValueError, TypeError):
                input_messages = []
        snippet_parts: list[str] = []
        for msg in input_messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    snippet_parts.append(content)
        full = " ".join(snippet_parts).strip()
        input_summary = full[:200]
        tool_calls = row.get("tool_calls") or []
        if isinstance(tool_calls, str):
            try:
                tool_calls = json.loads(tool_calls)
            except (ValueError, TypeError):
                tool_calls = []
        tool_calls_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        return ReplayCaseSummary(
            id=row["id"],
            captured_at=row["captured_at"],
            agent_handle=row["agent_handle"],
            model=row["model"],
            status=row["status"],
            input_summary=input_summary,
            output_text=row.get("output_text"),
            tool_calls_count=tool_calls_count,
            duration_ms=row.get("duration_ms"),
        )


def get_replay_case_repo(db: AsyncSession = Depends(get_db)) -> ReplayCaseRepository:
    """FastAPI Depends factory."""
    return ReplayCaseRepository(db)
