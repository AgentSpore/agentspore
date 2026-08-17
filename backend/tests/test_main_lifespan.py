"""Regression cover for finding 4: bg_tasks must be cancelled before close_redis.

background.py's ScheduledTask.start() releases its Redis leader lease in a
`finally` — including on CancelledError during shutdown. If `close_redis()`
runs BEFORE the background tasks are cancelled and awaited, that release
either never happens (the task is torn down mid-flight when the event loop
exits) or runs against an already-closed client and is silently swallowed
(background.py never raises on release failure) — leaving an orphan lease
that blocks the next worker for a full lock_ttl_s.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.main import lifespan


class _FastAppState:
    """Minimal stand-in for FastAPI's `app.state` — just needs attribute assignment."""


class _FastApp:
    def __init__(self) -> None:
        self.state = _FastAppState()


async def _never_ending_task() -> None:
    """Mirrors ScheduledTask.start(): runs forever until cancelled."""
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_bg_tasks_are_cancelled_and_awaited_before_close_redis():
    call_order: list[str] = []

    async def fake_close_redis():
        call_order.append("close_redis")

    bg_task = asyncio.create_task(_never_ending_task())
    original_cancel = bg_task.cancel

    def tracking_cancel():
        call_order.append("bg_task_cancelled")
        return original_cancel()

    bg_task.cancel = tracking_cancel

    with (
        patch("app.main.init_redis", AsyncMock()),
        patch("app.main.spawn_background_tasks", return_value=[bg_task]),
        patch("app.main.close_redis", fake_close_redis),
    ):
        app = _FastApp()
        async with lifespan(app):
            pass

    assert bg_task.cancelled() or bg_task.done(), "bg_task was not cancelled by lifespan shutdown"
    assert call_order == ["bg_task_cancelled", "close_redis"], (
        f"wrong shutdown order: {call_order} — bg_tasks must be cancelled "
        "and awaited BEFORE close_redis, or a task's release() runs "
        "against an already-closed client and is silently swallowed"
    )
