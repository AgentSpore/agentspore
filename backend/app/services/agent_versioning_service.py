"""Canary rollback metric collection and auto-rollback logic.

Nightly leader-locked job compares canary success_rate vs primary success_rate
per agent. When regression exceeds auto_rollback_threshold and canary has
at least MIN_CANARY_SAMPLES rows, the canary is automatically deactivated
(canary_version_id=NULL, canary_pct=0) and an audit log entry is written.

Regression formula:
    regression = primary_success_rate - canary_success_rate

If regression > threshold and canary_samples >= MIN_CANARY_SAMPLES → rollback.
"""

from __future__ import annotations

from uuid import UUID

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIN_CANARY_SAMPLES: int = 30
"""Minimum canary task_billing rows required before triggering rollback."""


class AgentVersioningService:
    """Business logic for canary metrics and auto-rollback."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def compute_canary_regression(self, agent_id: UUID) -> float | None:
        """Compute regression delta for agent's active canary.

        Returns:
            Float regression value (primary_sr - canary_sr) if canary is active
            and has >= MIN_CANARY_SAMPLES rows, else None.
        """
        row = await self._db.execute(
            text("""
                SELECT
                    acr.primary_version_id,
                    acr.canary_version_id,
                    acr.auto_rollback_threshold
                FROM agent_canary_routes acr
                WHERE acr.agent_id = :agent_id
                  AND acr.canary_version_id IS NOT NULL
                  AND acr.canary_pct > 0
            """),
            {"agent_id": str(agent_id)},
        )
        route = row.mappings().first()
        if route is None:
            return None

        primary_version_id = route["primary_version_id"]
        canary_version_id = route["canary_version_id"]

        # Canary sample count gate
        canary_count_row = await self._db.execute(
            text("""
                SELECT COUNT(*) AS cnt
                FROM task_billing
                WHERE agent_id = :agent_id
                  AND agent_version_id = :version_id::uuid
            """),
            {"agent_id": str(agent_id), "version_id": str(canary_version_id)},
        )
        canary_count = canary_count_row.scalar_one()
        if canary_count < MIN_CANARY_SAMPLES:
            logger.debug(
                "Canary gate: agent={} has only {} samples, need {}",
                agent_id, canary_count, MIN_CANARY_SAMPLES,
            )
            return None

        canary_sr = await self._success_rate(agent_id, canary_version_id)

        if primary_version_id is not None:
            primary_sr = await self._success_rate(agent_id, primary_version_id)
        else:
            # Primary without version: all rows with NULL agent_version_id
            primary_sr = await self._success_rate_null_version(agent_id)

        regression = primary_sr - canary_sr
        logger.debug(
            "Canary regression check: agent={} primary_sr={:.3f} canary_sr={:.3f} regression={:.3f}",
            agent_id, primary_sr, canary_sr, regression,
        )
        return regression

    async def auto_rollback_if_regressed(self, agent_id: UUID) -> bool:
        """Rollback canary if regression exceeds threshold.

        Returns:
            True if rollback was performed, False otherwise.
        """
        route_row = await self._db.execute(
            text("""
                SELECT auto_rollback_threshold, canary_version_id, primary_version_id
                FROM agent_canary_routes
                WHERE agent_id = :agent_id
            """),
            {"agent_id": str(agent_id)},
        )
        route = route_row.mappings().first()
        if route is None or route["canary_version_id"] is None:
            return False

        threshold = float(route["auto_rollback_threshold"])
        regression = await self.compute_canary_regression(agent_id)

        if regression is None or regression <= threshold:
            return False

        # Fetch current metrics for audit payload before reset
        canary_version_id = route["canary_version_id"]
        primary_version_id = route["primary_version_id"]
        canary_sr = await self._success_rate(agent_id, canary_version_id)
        if primary_version_id is not None:
            primary_sr = await self._success_rate(agent_id, primary_version_id)
        else:
            primary_sr = await self._success_rate_null_version(agent_id)

        # Perform rollback: clear canary
        await self._db.execute(
            text("""
                UPDATE agent_canary_routes
                SET canary_version_id = NULL,
                    canary_pct = 0,
                    updated_at = NOW()
                WHERE agent_id = :agent_id
            """),
            {"agent_id": str(agent_id)},
        )

        # Write audit log
        await self._db.execute(
            text("""
                INSERT INTO agent_audit_log (agent_id, event_type, payload)
                VALUES (:agent_id, 'agent.auto_rollback', :payload::jsonb)
            """),
            {
                "agent_id": str(agent_id),
                "payload": (
                    f'{{"primary_success_rate": {primary_sr:.4f},'
                    f' "canary_success_rate": {canary_sr:.4f},'
                    f' "regression": {regression:.4f},'
                    f' "threshold": {threshold:.4f},'
                    f' "rolled_back_version_id": "{canary_version_id}"}}'
                ),
            },
        )

        logger.warning(
            "Auto-rollback: agent={} regression={:.3f} > threshold={:.3f}. "
            "Canary {} deactivated.",
            agent_id, regression, threshold, canary_version_id,
        )
        return True

    async def nightly_canary_check_all(self) -> None:
        """Check every agent with an active canary and rollback regressions."""
        rows = await self._db.execute(
            text("""
                SELECT agent_id FROM agent_canary_routes
                WHERE canary_version_id IS NOT NULL AND canary_pct > 0
            """)
        )
        agent_ids: list[UUID] = [UUID(str(row["agent_id"])) for row in rows.mappings()]

        rolled_back = 0
        checked = 0
        for agent_id in agent_ids:
            try:
                did_rollback = await self.auto_rollback_if_regressed(agent_id)
                checked += 1
                if did_rollback:
                    rolled_back += 1
                    await self._db.commit()
                else:
                    # No structural change, but still commit audit writes if any
                    await self._db.rollback()
            except Exception as exc:
                logger.error("Canary check failed for agent={}: {}", agent_id, exc)
                await self._db.rollback()

        logger.info(
            "Nightly canary check: {} agents checked, {} auto-rolled-back",
            checked, rolled_back,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _success_rate(self, agent_id: UUID, version_id: UUID) -> float:
        """Success rate for a specific version_id (0.0–1.0)."""
        result = await self._db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE success = TRUE)::float / NULLIF(COUNT(*), 0) AS sr
                FROM task_billing
                WHERE agent_id = :agent_id
                  AND agent_version_id = :version_id::uuid
            """),
            {"agent_id": str(agent_id), "version_id": str(version_id)},
        )
        sr = result.scalar_one()
        return float(sr) if sr is not None else 1.0

    async def _success_rate_null_version(self, agent_id: UUID) -> float:
        """Success rate for rows with NULL agent_version_id (legacy primary)."""
        result = await self._db.execute(
            text("""
                SELECT
                    COUNT(*) FILTER (WHERE success = TRUE)::float / NULLIF(COUNT(*), 0) AS sr
                FROM task_billing
                WHERE agent_id = :agent_id
                  AND agent_version_id IS NULL
            """),
            {"agent_id": str(agent_id)},
        )
        sr = result.scalar_one()
        return float(sr) if sr is not None else 1.0
