"""Leader-lock lifecycle for `ScheduledTask` (no Docker, no real Redis).

Regression cover for the cadence defect: the lease used to be acquired with
`SET NX EX lock_ttl_s` and never released, so a task's own stale lock blocked
its next `SET NX` and the effective period became max(interval_s, lock_ttl_s).
On production that turned battle_run's 30-second reconcile into a 10-minute
one. These tests pin the fixed contract: the lease is held for the duration of
`run_once` and released — ownership-checked — the moment the cycle ends.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.core.background import ALL_TASKS, MIN_RENEW_INTERVAL_S, CronSchedulerTask, ScheduledTask

# A crashed worker must not park a task for longer than this. Lives in the test
# rather than in background.py because it is a review guard on the declared
# values, not a runtime knob: the old workaround pairing (lock_ttl_s ~=
# interval_s + 20) put mixer_cleanup at 3620 — a full hour of stalled cleanup
# after one crash — and nothing failed. Now it would.
MAX_CRASH_STALL_S = 300


class _FakeScript:
    """What redis-py's register_script returns: a callable bound to a script.

    Mirrors the compare-and-delete / compare-and-expire the real Lua does, so a
    test can assert the store the way production Redis would leave it.
    """

    def __init__(self, redis: FakeRedis, script: str) -> None:
        self._redis = redis
        self._script = script

    async def __call__(self, keys: list[str], args: list[str]):
        key, token = keys[0], args[0]
        self._redis.evals.append(self._script)
        if self._redis.store.get(key) != token:
            return 0
        if "del" in self._script:
            del self._redis.store[key]
            return 1
        return 1  # expire / renew


class FakeRedis:
    """Minimal Redis double: SET NX + the two Lua scripts the task runs.

    `eval` covers the renew path (unchanged production code at line ~179);
    `register_script` covers the release path (the typed script API). Both feed
    the same `evals` list so a test can assert "no Lua ran against the store".

    Deliberately does NOT implement key expiry. Expiry is a fallback for a
    crashed worker; a healthy loop's cadence must come from the explicit
    release, and ignoring `ex` is what makes that testable in milliseconds.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.evals: list[str] = []

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *args):
        key, token = args[0], args[1]
        self.evals.append(script)
        if self.store.get(key) != token:
            return 0
        if "del" in script:
            del self.store[key]
            return 1
        return 1  # expire / renew

    def register_script(self, script: str) -> _FakeScript:
        return _FakeScript(self, script)


class _CountingTask(ScheduledTask):
    """run_once takes LONGER than one interval — the shape that exposed the bug."""

    name = "test_counting"
    interval_s = 0.01  # type: ignore[assignment]  # float is fine for asyncio.sleep
    lock_ttl_s = 30  # generous crash bound, must not become the cadence

    def __init__(self, work_s: float = 0.03, boom: bool = False) -> None:
        super().__init__()
        self.runs = 0
        self.work_s = work_s
        self.boom = boom

    async def run_once(self) -> None:
        self.runs += 1
        await asyncio.sleep(self.work_s)
        if self.boom:
            raise RuntimeError("run_once exploded")


async def _run_for(task: ScheduledTask, seconds: float) -> None:
    """Drive the real `start()` loop for a bounded wall time, then cancel it."""
    runner = asyncio.create_task(task.start())
    await asyncio.sleep(seconds)
    runner.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runner


@pytest.mark.asyncio
async def test_slow_run_once_keeps_interval_cadence_not_ttl_cadence():
    """The defect: only ONE cycle ever ran, because the lease outlived it."""
    task = _CountingTask()
    redis = FakeRedis()
    with patch("app.core.background.get_redis", AsyncMock(return_value=redis)):
        await _run_for(task, 0.3)

    # Each cycle costs ~work_s + interval_s = 40ms, so ~7 in 300ms. Assert well
    # above 1 (the buggy count) but below the theoretical max, to stay stable
    # on a loaded CI box.
    assert task.runs >= 3, f"cadence collapsed to the TTL: only {task.runs} run(s)"


@pytest.mark.asyncio
async def test_lease_is_released_after_run_once_returns():
    task = _CountingTask(work_s=0)
    redis = FakeRedis()
    with patch("app.core.background.get_redis", AsyncMock(return_value=redis)):
        assert await task._acquire_leader() is True
        assert redis.store  # held during the cycle
        await task._release_leader()

    assert redis.store == {}
    assert task._lock_token is None


@pytest.mark.asyncio
async def test_lease_is_released_even_when_run_once_raises():
    """A crashing cycle must not wedge the task for a whole lock_ttl_s."""
    task = _CountingTask(work_s=0, boom=True)
    redis = FakeRedis()
    with patch("app.core.background.get_redis", AsyncMock(return_value=redis)):
        await _run_for(task, 0.15)

    assert task.runs >= 2, "a raising run_once left the lease behind"
    assert redis.store == {}, "lease survived the exception"


@pytest.mark.asyncio
async def test_release_never_deletes_a_lease_owned_by_another_worker():
    """TTL expired mid-run, someone else took the key — we must not delete it."""
    task = _CountingTask(work_s=0)
    redis = FakeRedis()
    with patch("app.core.background.get_redis", AsyncMock(return_value=redis)):
        assert await task._acquire_leader() is True
        # Simulate: our lease expired and worker B acquired a fresh one.
        redis.store[task._lock_key()] = "worker-b-token"
        await task._release_leader()

    assert redis.store[task._lock_key()] == "worker-b-token"


class _NoLockTask(_CountingTask):
    """CronSchedulerTask's shape: row-level claims, leader gate disabled."""

    name = "test_no_lock"
    lock_ttl_s = None

    async def run_once(self) -> None:
        self.runs += 1


@pytest.mark.asyncio
async def test_lock_ttl_none_bypasses_the_gate_entirely():
    task = _NoLockTask()
    redis = FakeRedis()
    # A key is already held — with the gate active this would block every cycle.
    redis.store[task._lock_key()] = "someone-else"
    with patch("app.core.background.get_redis", AsyncMock(return_value=redis)):
        await _run_for(task, 0.1)

    assert task.runs >= 2
    assert redis.evals == [], "an ungated task must not touch the lease at all"
    assert redis.store[task._lock_key()] == "someone-else"


def test_every_ttl_leaves_room_for_at_least_three_renewals():
    """Lower bound: a TTL too small to renew loses the lease to a Redis blip.

    Renewal fires every `lock_ttl_s // 3`, so a TTL below 3x the floor means
    the first renewal is already inside round-trip noise. A future edit that
    picks a too-small number must fail here rather than silently drop leases
    mid-run — the failure mode is a SECOND worker entering a cycle the first
    one is still executing, which no other test in this file would catch.
    """
    too_tight = {
        t.__name__: t.lock_ttl_s
        for t in ALL_TASKS
        if t.lock_ttl_s is not None and t.lock_ttl_s < 3 * MIN_RENEW_INTERVAL_S
    }
    assert too_tight == {}, f"lock_ttl_s below the renewal floor: {too_tight}"


def test_no_task_lets_a_crash_stall_it_for_longer_than_the_cap():
    """Upper bound: lock_ttl_s is how long a CRASHED worker blocks its peer.

    Pins the meaning the retune gave it. Reverting any task to the old
    `interval_s + 20` pairing (620 / 320 / 3620) fails here, which is the point
    — that convention was a workaround for the missing release, and it read as
    intentional design for long enough to cost a production incident.
    """
    too_slack = {
        t.__name__: t.lock_ttl_s
        for t in ALL_TASKS
        if t.lock_ttl_s is not None and t.lock_ttl_s > MAX_CRASH_STALL_S
    }
    assert too_slack == {}, f"a crash would stall these beyond the cap: {too_slack}"


def test_cron_scheduler_keeps_its_gate_disabled():
    """`lock_ttl_s = None` is deliberate (row-level FOR UPDATE SKIP LOCKED).

    Guard against a future bulk retune of the TTLs sweeping this one up: giving
    cron_scheduler a leader lock would REMOVE the exactly-once guarantee's
    fast-failover property, not add safety.
    """
    assert CronSchedulerTask.lock_ttl_s is None
    assert CronSchedulerTask in ALL_TASKS


@pytest.mark.asyncio
async def test_redis_outage_still_fails_open_and_release_stays_silent():
    """Fail-open acquire is preserved, and the release path cannot raise."""
    task = _CountingTask(work_s=0)
    down = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("app.core.background.get_redis", down):
        assert await task._acquire_leader() is True  # fail-open
        assert task._lock_token is None  # no lease to release
        await task._release_leader()  # must not raise
        await _run_for(task, 0.1)

    assert task.runs >= 2
