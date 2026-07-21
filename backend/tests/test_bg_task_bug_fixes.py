"""Regression tests for two background-task defects.

Bug 1 — ``MixerCleanupTask.run_once`` called ``get_mixer_service(db)``, a
FastAPI-DI factory. Outside a request its ``repo=Depends(...)`` default stays the
``Depends`` marker, so ``cleanup_expired`` blew up on
``self.repo.get_expired_sessions()`` every cycle. The fix builds the service
directly: ``MixerService(db, MixerRepository(db))``.

Bug 2 — ``_claim_demo_drive`` read ``getattr(gate, "_redis", None)``; ``LLMGate``
has no ``_redis`` attribute, so the claim was always ``None`` → always fail-open →
never deduped, and all 4 uvicorn workers paid for the same demo answer. The fix
pulls the shared ``get_redis`` singleton and performs a real ``SET NX``.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.core.background import MixerCleanupTask
from app.services.battle_runner import _claim_demo_drive


@contextlib.asynccontextmanager
async def _fake_session(db):
    yield db


# ── Bug 1 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mixer_cleanup_calls_repo_not_depends_marker():
    """run_once must reach a REAL repository method, not a Depends marker.

    Mutation: revert run_once to ``get_mixer_service(db)`` — ``self.repo`` becomes
    the ``Depends`` marker and ``cleanup_expired`` raises
    ``AttributeError: 'Depends' object has no attribute 'get_expired_sessions'``,
    so this test goes red.
    """
    db = AsyncMock()

    with (
        patch(
            "app.core.background.async_session_maker",
            return_value=_fake_session(db),
        ),
        patch(
            "app.repositories.mixer_repo.MixerRepository.get_expired_sessions",
            new_callable=AsyncMock,
            return_value=[],
        ) as get_expired,
    ):
        await MixerCleanupTask().run_once()

    get_expired.assert_awaited_once()
    db.commit.assert_awaited_once()


# ── Bug 2 ──────────────────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal ``SET NX`` — first winner gets the key, later callers get None."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True


@pytest.mark.asyncio
async def test_claim_demo_drive_dedups_across_callers():
    """First caller wins the claim, second loses it — a real cross-process dedup.

    Mutation: revert to ``getattr(gate, "_redis", None)`` — the gate has no
    ``_redis``, the claim is always fail-open True, and the second assertion
    (expecting False) goes red.
    """
    redis = _FakeRedis()
    gate = object()

    with patch("app.services.battle_runner.get_redis", AsyncMock(return_value=redis)):
        first = await _claim_demo_drive(gate, "battle-1")
        second = await _claim_demo_drive(gate, "battle-1")

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_claim_demo_drive_fails_open_when_redis_uninitialised():
    """No Redis (unit tests / outage) → get_redis raises → fail-open True."""
    with patch(
        "app.services.battle_runner.get_redis",
        AsyncMock(side_effect=RuntimeError("Redis not initialized")),
    ):
        assert await _claim_demo_drive(object(), "battle-1") is True
