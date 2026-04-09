"""AgentWebhookService — outbound HTTP delivery to agent webhook URLs.

Used as a fallback channel when an agent has no active WebSocket and is
unreachable via Redis pub/sub (e.g. AWS Lambda, Cloud Functions, Vercel —
serverless environments that can't keep WS connections open).

Features:
- HMAC-SHA256 signing with the agent's webhook_secret
  (header X-AgentSpore-Signature: sha256=<hex>)
- Retry with exponential backoff (3 attempts: 1s, 5s, 15s)
- Auto-disable webhook after N consecutive failures
- Dead letter queue insertion on final failure (replay on reconnect)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_maker


class AgentWebhookService:
    """Delivers events to agent webhook URLs with retry + dead letter queue."""

    MAX_ATTEMPTS = 3
    BACKOFF_SECONDS = (1, 5, 15)
    REQUEST_TIMEOUT = 10.0
    AUTO_DISABLE_AFTER = 10  # consecutive failures

    @staticmethod
    def sign(secret: str, payload: bytes) -> str:
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    @classmethod
    async def fetch_webhook(cls, db: AsyncSession, agent_id: str) -> dict | None:
        row = await db.execute(
            text("""
                SELECT webhook_url, webhook_secret, webhook_failures_count, webhook_disabled
                FROM agents
                WHERE id = :id
            """),
            {"id": agent_id},
        )
        r = row.first()
        if not r or not r[0] or r[3]:
            return None
        return {"url": r[0], "secret": r[1] or "", "failures": r[2] or 0}

    @classmethod
    async def deliver(cls, agent_id: str, event: dict[str, Any]) -> bool:
        """Try to deliver an event to the agent's webhook. Returns True on success."""
        async with async_session_maker() as db:
            cfg = await cls.fetch_webhook(db, agent_id)
            if not cfg:
                return False

            event_id = event.get("id") or str(uuid.uuid4())
            body = {**event, "id": event_id}
            payload = json.dumps(body).encode()
            headers = {
                "Content-Type": "application/json",
                "X-AgentSpore-Event": event.get("type", "unknown"),
                "X-AgentSpore-Event-Id": event_id,
            }
            if cfg["secret"]:
                headers["X-AgentSpore-Signature"] = f"sha256={cls.sign(cfg['secret'], payload)}"

            last_error: str | None = None
            for attempt, delay in enumerate(cls.BACKOFF_SECONDS, start=1):
                try:
                    async with httpx.AsyncClient(timeout=cls.REQUEST_TIMEOUT) as client:
                        resp = await client.post(cfg["url"], content=payload, headers=headers)
                    if 200 <= resp.status_code < 300:
                        await cls._reset_failures(db, agent_id)
                        return True
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                except Exception as e:
                    last_error = f"{type(e).__name__}: {str(e)[:200]}"

                if attempt < cls.MAX_ATTEMPTS:
                    await asyncio.sleep(delay)

            await cls._record_failure(db, agent_id, cfg["failures"] + 1)
            await cls._dead_letter(db, agent_id, event_id, body, last_error)
            logger.warning(
                "Webhook delivery failed for agent={} event={}: {}",
                agent_id, event.get("type"), last_error,
            )
            return False

    @classmethod
    async def _reset_failures(cls, db: AsyncSession, agent_id: str) -> None:
        await db.execute(
            text("UPDATE agents SET webhook_failures_count = 0 WHERE id = :id"),
            {"id": agent_id},
        )
        await db.commit()

    @classmethod
    async def _record_failure(cls, db: AsyncSession, agent_id: str, failures: int) -> None:
        disable = failures >= cls.AUTO_DISABLE_AFTER
        await db.execute(
            text("""
                UPDATE agents
                SET webhook_failures_count = :f,
                    webhook_last_failure_at = NOW(),
                    webhook_disabled = CASE WHEN :disable THEN TRUE ELSE webhook_disabled END
                WHERE id = :id
            """),
            {"id": agent_id, "f": failures, "disable": disable},
        )
        await db.commit()
        if disable:
            logger.warning("Webhook auto-disabled for agent={} after {} failures", agent_id, failures)

    @classmethod
    async def _dead_letter(
        cls,
        db: AsyncSession,
        agent_id: str,
        event_id: str,
        event: dict[str, Any],
        error: str | None,
    ) -> None:
        try:
            await db.execute(
                text("""
                    INSERT INTO webhook_dead_letter (agent_id, event_id, event_type, payload, last_error, attempts)
                    VALUES (:agent_id, :event_id, :event_type, :payload, :error, :attempts)
                    ON CONFLICT (agent_id, event_id) DO UPDATE
                    SET attempts = webhook_dead_letter.attempts + EXCLUDED.attempts,
                        last_error = EXCLUDED.last_error
                """),
                {
                    "agent_id": agent_id,
                    "event_id": event_id,
                    "event_type": event.get("type", "unknown"),
                    "payload": json.dumps(event),
                    "error": error,
                    "attempts": cls.MAX_ATTEMPTS,
                },
            )
            await db.commit()
        except Exception as e:
            logger.error("Failed to insert into webhook_dead_letter: {}", e)
