"""CouncilRepository — DB access for councils, panelists, messages, votes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db


class CouncilRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Councils ──────────────────────────────────────────────────────

    async def create(
        self,
        topic: str,
        brief: str,
        mode: str,
        panel_size: int,
        max_rounds: int,
        max_tokens_per_msg: int,
        timebox_seconds: int,
        convener_user_id: str | None = None,
        convener_agent_id: str | None = None,
        convener_ip: str | None = None,
        is_public: bool = True,
    ) -> dict:
        row = (await self.db.execute(
            text("""
                INSERT INTO councils
                    (topic, brief, mode, panel_size, max_rounds, max_tokens_per_msg,
                     timebox_seconds, convener_user_id, convener_agent_id, convener_ip, is_public)
                VALUES
                    (:topic, :brief, :mode, :panel_size, :max_rounds, :max_tokens_per_msg,
                     :timebox_seconds, :cu, :ca, :ip, :is_public)
                RETURNING *
            """),
            {
                "topic": topic, "brief": brief, "mode": mode,
                "panel_size": panel_size, "max_rounds": max_rounds,
                "max_tokens_per_msg": max_tokens_per_msg, "timebox_seconds": timebox_seconds,
                "cu": convener_user_id, "ca": convener_agent_id, "ip": convener_ip,
                "is_public": is_public,
            },
        )).mappings().first()
        return dict(row) if row else {}

    async def get_by_id(self, council_id: str) -> dict | None:
        row = (await self.db.execute(
            text("SELECT * FROM councils WHERE id = CAST(:id AS UUID)"),
            {"id": council_id},
        )).mappings().first()
        return dict(row) if row else None

    async def update_status(
        self, council_id: str, status: str, *,
        round_num: int | None = None,
        started: bool = False,
        ended: bool = False,
        resolution: str | None = None,
        consensus_score: float | None = None,
    ) -> None:
        sets = ["status = :status"]
        params: dict[str, Any] = {"id": council_id, "status": status}
        if round_num is not None:
            sets.append("current_round = :rn")
            params["rn"] = round_num
        if started:
            sets.append("started_at = COALESCE(started_at, NOW())")
        if ended:
            sets.append("ended_at = NOW()")
        if resolution is not None:
            sets.append("resolution = :res")
            params["res"] = resolution
        if consensus_score is not None:
            sets.append("consensus_score = :cs")
            params["cs"] = consensus_score
        await self.db.execute(
            text(f"UPDATE councils SET {', '.join(sets)} WHERE id = CAST(:id AS UUID)"),
            params,
        )

    async def list_public(self, limit: int = 20, offset: int = 0) -> list[dict]:
        rows = (await self.db.execute(
            text("""
                SELECT id, topic, status, mode, panel_size, current_round, max_rounds,
                       consensus_score, created_at, ended_at
                FROM councils
                WHERE is_public = TRUE
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows]

    # ── Panelists ─────────────────────────────────────────────────────

    async def add_panelist(
        self,
        council_id: str,
        *,
        adapter: str,
        display_name: str,
        role: str = "panelist",
        agent_id: str | None = None,
        model_id: str | None = None,
        perspective: str | None = None,
    ) -> dict:
        row = (await self.db.execute(
            text("""
                INSERT INTO council_panelists
                    (council_id, adapter, agent_id, model_id, display_name, role, perspective)
                VALUES
                    (CAST(:cid AS UUID), :adapter, CAST(:aid AS UUID), :mid, :name, :role, :persp)
                RETURNING *
            """),
            {
                "cid": council_id, "adapter": adapter,
                "aid": agent_id, "mid": model_id,
                "name": display_name, "role": role, "persp": perspective,
            },
        )).mappings().first()
        return dict(row) if row else {}

    async def list_panelists(self, council_id: str) -> list[dict]:
        rows = (await self.db.execute(
            text("SELECT * FROM council_panelists WHERE council_id = CAST(:cid AS UUID) ORDER BY joined_at"),
            {"cid": council_id},
        )).mappings().all()
        return [dict(r) for r in rows]

    async def mark_spoke(self, panelist_id: str, round_num: int) -> None:
        await self.db.execute(
            text("UPDATE council_panelists SET last_spoke_round = :rn WHERE id = CAST(:pid AS UUID)"),
            {"pid": panelist_id, "rn": round_num},
        )

    # ── Messages ──────────────────────────────────────────────────────

    async def add_message(
        self,
        council_id: str,
        *,
        kind: str,
        content: str,
        round_num: int = 0,
        panelist_id: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        import json as _json
        row = (await self.db.execute(
            text("""
                INSERT INTO council_messages
                    (council_id, panelist_id, round_num, kind, content, meta)
                VALUES
                    (CAST(:cid AS UUID), CAST(:pid AS UUID), :rn, :kind, :content, CAST(:meta AS JSONB))
                RETURNING *
            """),
            {
                "cid": council_id, "pid": panelist_id, "rn": round_num,
                "kind": kind, "content": content,
                "meta": _json.dumps(meta) if meta is not None else None,
            },
        )).mappings().first()
        return dict(row) if row else {}

    async def list_messages(self, council_id: str, after_id: str | None = None) -> list[dict]:
        if after_id:
            rows = (await self.db.execute(
                text("""
                    SELECT * FROM council_messages
                    WHERE council_id = CAST(:cid AS UUID)
                      AND created_at > (SELECT created_at FROM council_messages WHERE id = CAST(:aid AS UUID))
                    ORDER BY created_at
                """),
                {"cid": council_id, "aid": after_id},
            )).mappings().all()
        else:
            rows = (await self.db.execute(
                text("SELECT * FROM council_messages WHERE council_id = CAST(:cid AS UUID) ORDER BY created_at"),
                {"cid": council_id},
            )).mappings().all()
        return [dict(r) for r in rows]

    # ── Votes ─────────────────────────────────────────────────────────

    async def cast_vote(
        self, council_id: str, panelist_id: str, vote: str,
        confidence: float = 1.0, reasoning: str | None = None,
    ) -> None:
        await self.db.execute(
            text("""
                INSERT INTO council_votes (council_id, panelist_id, vote, confidence, reasoning)
                VALUES (CAST(:cid AS UUID), CAST(:pid AS UUID), :vote, :conf, :reason)
                ON CONFLICT (council_id, panelist_id) DO UPDATE
                SET vote = EXCLUDED.vote,
                    confidence = EXCLUDED.confidence,
                    reasoning = EXCLUDED.reasoning,
                    voted_at = NOW()
            """),
            {"cid": council_id, "pid": panelist_id, "vote": vote, "conf": confidence, "reason": reasoning},
        )

    async def list_votes(self, council_id: str) -> list[dict]:
        rows = (await self.db.execute(
            text("SELECT * FROM council_votes WHERE council_id = CAST(:cid AS UUID)"),
            {"cid": council_id},
        )).mappings().all()
        return [dict(r) for r in rows]


def get_council_repo(db: AsyncSession = Depends(get_db)) -> CouncilRepository:
    return CouncilRepository(db)
