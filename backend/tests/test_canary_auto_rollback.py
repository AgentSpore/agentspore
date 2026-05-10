"""Unit tests for canary auto-rollback metric collection.

All tests use AsyncMock — no Docker / Testcontainers required.
Covers:
- regression detected → rollback triggered
- both versions healthy → no rollback
- canary sample count below MIN_CANARY_SAMPLES → no rollback
- audit log entry written on rollback
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID, uuid4

import pytest

from app.services.agent_versioning_service import MIN_CANARY_SAMPLES, AgentVersioningService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scalar_mock(value):
    """db.execute() mock whose .scalar_one() returns `value`."""
    m = MagicMock()
    m.scalar_one.return_value = value
    return m


def _mappings_first_mock(row: dict | None):
    """db.execute() mock whose .mappings().first() returns `row`."""
    m = MagicMock()
    m.mappings.return_value.first.return_value = row
    return m


def _mappings_all_mock(rows: list[dict]):
    """db.execute() mock whose .mappings() is iterable (nightly_canary_check_all)."""
    m = MagicMock()
    m.mappings.return_value = rows
    return m


def _route_row(
    agent_id: UUID,
    canary_version_id: UUID | None,
    primary_version_id: UUID | None,
    threshold: float = 0.1,
    canary_pct: int = 10,
) -> dict:
    return {
        "agent_id": agent_id,
        "canary_version_id": canary_version_id,
        "primary_version_id": primary_version_id,
        "auto_rollback_threshold": threshold,
        "canary_pct": canary_pct,
    }


# ---------------------------------------------------------------------------
# Test: regression detected → rollback triggered
# ---------------------------------------------------------------------------

class TestRegressionDetectedRollback:
    """30 primary OK + 30 canary failures → regression detected, rollback triggered."""

    @pytest.mark.asyncio
    async def test_rollback_triggered_on_regression(self):
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()
        threshold = 0.1

        db = AsyncMock()

        # Call sequence for auto_rollback_if_regressed:
        # 1) route SELECT (outer call in auto_rollback_if_regressed)
        # 2) route SELECT in compute_canary_regression (canary active check)
        # 3) canary count (>= MIN_CANARY_SAMPLES)
        # 4) canary success_rate  (0 / 30 = 0.0)
        # 5) primary success_rate (30 / 30 = 1.0)
        # 6) fetch metrics again in auto_rollback_if_regressed post-gate
        #    (same sub-calls: canary_sr, primary_sr — reuse same mocks)
        # 7) UPDATE (rollback)
        # 8) INSERT audit log

        # primary_sr=1.0, canary_sr=0.0 → regression=1.0 > threshold=0.1

        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=threshold)

        # Build side_effect list for db.execute calls
        # auto_rollback_if_regressed → SELECT route
        # compute_canary_regression → SELECT route (canary active check)
        # compute_canary_regression → COUNT canary samples
        # compute_canary_regression → canary SR
        # compute_canary_regression → primary SR
        # auto_rollback_if_regressed → canary SR (for audit payload)
        # auto_rollback_if_regressed → primary SR (for audit payload)
        # auto_rollback_if_regressed → UPDATE
        # auto_rollback_if_regressed → INSERT audit

        def _sr_scalar(success_count, total):
            rate = success_count / total if total else None
            return _scalar_mock(rate)

        db.execute.side_effect = [
            _mappings_first_mock(route_row),                      # outer route SELECT
            _mappings_first_mock(route_row),                      # inner route SELECT (compute)
            _scalar_mock(MIN_CANARY_SAMPLES),                     # canary count gate
            _sr_scalar(0, MIN_CANARY_SAMPLES),                    # canary SR (0.0)
            _sr_scalar(MIN_CANARY_SAMPLES, MIN_CANARY_SAMPLES),   # primary SR (1.0)
            _sr_scalar(0, MIN_CANARY_SAMPLES),                    # canary SR for audit
            _sr_scalar(MIN_CANARY_SAMPLES, MIN_CANARY_SAMPLES),   # primary SR for audit
            MagicMock(),                                           # UPDATE
            MagicMock(),                                           # INSERT audit
        ]

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is True
        # UPDATE must have been called
        update_calls = [str(c.args[0]) for c in db.execute.call_args_list]
        assert any("UPDATE agent_canary_routes" in s for s in update_calls)

    @pytest.mark.asyncio
    async def test_audit_log_written_on_rollback(self):
        """audit log INSERT executed when rollback happens."""
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()

        db = AsyncMock()
        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=0.05)

        db.execute.side_effect = [
            _mappings_first_mock(route_row),
            _mappings_first_mock(route_row),
            _scalar_mock(MIN_CANARY_SAMPLES),
            _scalar_mock(0.0),   # canary SR
            _scalar_mock(1.0),   # primary SR
            _scalar_mock(0.0),   # canary SR audit
            _scalar_mock(1.0),   # primary SR audit
            MagicMock(),         # UPDATE
            MagicMock(),         # INSERT audit log
        ]

        svc = AgentVersioningService(db)
        await svc.auto_rollback_if_regressed(agent_id)

        calls_sql = [str(c.args[0]) for c in db.execute.call_args_list]
        assert any("agent_audit_log" in s for s in calls_sql)
        assert any("agent.auto_rollback" in s for s in calls_sql)


# ---------------------------------------------------------------------------
# Test: both versions healthy → no rollback
# ---------------------------------------------------------------------------

class TestBothHealthyNoRollback:
    """30 primary OK + 30 canary OK → regression=0.0 ≤ threshold → no rollback."""

    @pytest.mark.asyncio
    async def test_no_rollback_when_canary_healthy(self):
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()

        db = AsyncMock()
        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=0.1)

        db.execute.side_effect = [
            _mappings_first_mock(route_row),               # outer route SELECT
            _mappings_first_mock(route_row),               # inner compute route SELECT
            _scalar_mock(MIN_CANARY_SAMPLES),              # canary count
            _scalar_mock(1.0),                             # canary SR (perfect)
            _scalar_mock(1.0),                             # primary SR (perfect)
        ]

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is False
        calls_sql = [str(c.args[0]) for c in db.execute.call_args_list]
        assert not any("UPDATE agent_canary_routes" in s for s in calls_sql)

    @pytest.mark.asyncio
    async def test_no_rollback_when_regression_exactly_at_threshold(self):
        """regression == threshold is not > threshold, so no rollback."""
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()

        db = AsyncMock()
        threshold = 0.1
        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=threshold)

        # primary_sr=1.0, canary_sr=0.9 → regression=0.1 == threshold → no rollback
        db.execute.side_effect = [
            _mappings_first_mock(route_row),
            _mappings_first_mock(route_row),
            _scalar_mock(MIN_CANARY_SAMPLES),
            _scalar_mock(0.9),
            _scalar_mock(1.0),
        ]

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is False


# ---------------------------------------------------------------------------
# Test: < MIN_CANARY_SAMPLES → no rollback even if all canary failed
# ---------------------------------------------------------------------------

class TestInsufficientCanarySamples:
    """5 canary samples < 30 → skip rollback even with 100% canary failure."""

    @pytest.mark.asyncio
    async def test_no_rollback_when_below_min_samples(self):
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()

        db = AsyncMock()
        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=0.05)

        db.execute.side_effect = [
            _mappings_first_mock(route_row),    # outer route SELECT
            _mappings_first_mock(route_row),    # inner compute route SELECT
            _scalar_mock(5),                    # canary count = 5 < MIN_CANARY_SAMPLES
            # No more calls — gate returns None early
        ]

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is False
        # Only 3 db.execute calls — gate stopped early
        assert db.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_no_rollback_at_zero_samples(self):
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()

        db = AsyncMock()
        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=0.0)

        db.execute.side_effect = [
            _mappings_first_mock(route_row),
            _mappings_first_mock(route_row),
            _scalar_mock(0),     # 0 samples
        ]

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is False


# ---------------------------------------------------------------------------
# Test: no active canary → short-circuit
# ---------------------------------------------------------------------------

class TestNoActiveCanary:
    """Agent has no canary route → returns False immediately."""

    @pytest.mark.asyncio
    async def test_no_rollback_without_canary_route(self):
        agent_id = uuid4()
        db = AsyncMock()
        db.execute.return_value = _mappings_first_mock(None)

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_no_rollback_canary_version_null(self):
        """Route exists but canary_version_id is NULL (already rolled back)."""
        agent_id = uuid4()
        db = AsyncMock()
        row = _route_row(agent_id, canary_version_id=None, primary_version_id=uuid4())
        db.execute.return_value = _mappings_first_mock(row)

        svc = AgentVersioningService(db)
        result = await svc.auto_rollback_if_regressed(agent_id)

        assert result is False


# ---------------------------------------------------------------------------
# Test: compute_canary_regression returns None when gate not met
# ---------------------------------------------------------------------------

class TestComputeCanaryRegression:

    @pytest.mark.asyncio
    async def test_returns_none_when_no_active_canary(self):
        agent_id = uuid4()
        db = AsyncMock()
        db.execute.return_value = _mappings_first_mock(None)

        svc = AgentVersioningService(db)
        regression = await svc.compute_canary_regression(agent_id)

        assert regression is None

    @pytest.mark.asyncio
    async def test_returns_float_when_regression_present(self):
        agent_id = uuid4()
        canary_vid = uuid4()
        primary_vid = uuid4()
        db = AsyncMock()
        route_row = _route_row(agent_id, canary_vid, primary_vid, threshold=0.1)

        db.execute.side_effect = [
            _mappings_first_mock(route_row),
            _scalar_mock(MIN_CANARY_SAMPLES),
            _scalar_mock(0.5),   # canary SR
            _scalar_mock(0.9),   # primary SR
        ]

        svc = AgentVersioningService(db)
        regression = await svc.compute_canary_regression(agent_id)

        assert regression is not None
        assert abs(regression - 0.4) < 1e-6
