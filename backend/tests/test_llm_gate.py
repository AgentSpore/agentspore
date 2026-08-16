"""Tests for backend/app/services/llm_gate.py — step 9's account-wide gate.

The invariant under test, stated so it can be falsified:

    At most three platform LLM calls are in flight on the z.ai account at any
    instant, ACROSS PROCESSES, and a slot is never lost by a live caller nor
    held forever by a dead one.

Why the headline test spawns real processes: the property that matters is
"per ACCOUNT", and the backend runs multiple worker processes against one
account. An ``asyncio.Semaphore`` would pass any single-process test perfectly
while capping the account at 3xN in production. Six coroutines in one event loop
therefore prove nothing about the thing we actually need, so the test uses
``multiprocessing`` against one real Redis, with a counter maintained OUTSIDE
the gate's own data structures — asking the gate to report its own compliance
would be circular.

Docker is required (@pytest.mark.integration): the gate is Lua-on-Redis, and
its atomicity is a property of Redis executing that script indivisibly. A fake
Redis would only prove that a fake behaves as written.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import time

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

from app.services.llm_gate import (
    DEFAULT_MAX_CONCURRENCY,
    PROVIDER_MIN_INTERVAL_SECONDS,
    ZAI_ACCOUNT_KEY,
    ZAI_MAX_CONCURRENCY,
    GateSlot,
    LLMGate,
    LLMGateTimeoutError,
    provider_account_key,
    provider_capacity,
)

pytestmark = pytest.mark.integration

# The externally-observed concurrency counter. It lives in Redis because the
# processes must share it, but it is NOT the gate's sorted set: it is
# incremented by the guarded body itself, so it measures what the account would
# actually see rather than what the gate believes it permitted.
OBSERVED_KEY = "test:observed:inflight"
OBSERVED_PEAK_KEY = "test:observed:peak"

# Every worker holds its slot this long, guaranteeing overlap: with 4 processes
# x 3 calls and a cap of 3, contention is certain rather than incidental.
HOLD_SECONDS = 0.25

WORKER_PROCESSES = 4
CALLS_PER_WORKER = 3


@pytest.fixture(scope="module")
def redis_container():
    """One real Redis for the module. Lua atomicity is the thing under test."""
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="module")
def redis_url(redis_container: RedisContainer) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def redis(redis_url: str):
    client = Redis.from_url(redis_url, decode_responses=True)
    await client.flushall()
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def gate(redis: Redis) -> LLMGate:
    return LLMGate(redis, key="test:gate", capacity=ZAI_MAX_CONCURRENCY, lease_seconds=30)


# -- the multi-process worker ------------------------------------------------
# Module level because `spawn` (the macOS default) re-imports and must be able
# to find it by name; a closure or a local function cannot be pickled.


def _worker_body(redis_url: str, key: str, capacity: int, calls: int) -> int:
    """Run `calls` gated 'requests', reporting the peak concurrency it saw."""
    return asyncio.run(_worker_async(redis_url, key, capacity, calls))


async def _worker_async(redis_url: str, key: str, capacity: int, calls: int) -> int:
    client = Redis.from_url(redis_url, decode_responses=True)
    gate = LLMGate(client, key=key, capacity=capacity, lease_seconds=30)
    local_peak = 0
    try:
        for _ in range(calls):
            async with gate.slot(wait_seconds=30.0):
                # The guarded body stands in for the z.ai HTTP call. It counts
                # itself in, observes, sleeps to force overlap, counts out.
                current = int(await client.incr(OBSERVED_KEY))
                local_peak = max(local_peak, current)
                # Record the peak globally too, so no single worker's view is
                # trusted to have witnessed the worst moment.
                await client.eval(
                    "local p = tonumber(redis.call('GET', KEYS[1]) or '0') "
                    "if tonumber(ARGV[1]) > p then redis.call('SET', KEYS[1], ARGV[1]) end "
                    "return 1",
                    1,
                    OBSERVED_PEAK_KEY,
                    str(current),
                )
                await asyncio.sleep(HOLD_SECONDS)
                await client.decr(OBSERVED_KEY)
    finally:
        await client.aclose()
    return local_peak


class TestAccountWideCap:
    """The headline property, proven across process boundaries."""

    @pytest.mark.asyncio
    async def test_llm_gate_caps_three_requests_across_multiple_processes(
        self, redis: Redis, redis_url: str
    ) -> None:
        await redis.set(OBSERVED_KEY, 0)
        await redis.set(OBSERVED_PEAK_KEY, 0)

        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(WORKER_PROCESSES) as pool:
            args = [
                (redis_url, "test:gate", ZAI_MAX_CONCURRENCY, CALLS_PER_WORKER)
            ] * WORKER_PROCESSES
            peaks = pool.starmap(_worker_body, args)

        observed_peak = int(await redis.get(OBSERVED_PEAK_KEY))

        # The invariant. 12 calls across 4 OS processes never put more than 3
        # bodies inside the gate simultaneously.
        assert observed_peak <= ZAI_MAX_CONCURRENCY, f"account saw {observed_peak} concurrent calls"
        assert max(peaks) <= ZAI_MAX_CONCURRENCY

        # Guard against a gate that passes by admitting nobody: with 4 workers
        # contending and a 0.25s hold, the cap must actually be REACHED. Without
        # this a `return 0` in the Lua would score a perfect pass.
        assert observed_peak == ZAI_MAX_CONCURRENCY, (
            "contention never reached the cap — test is not proving the bound"
        )

        # Every call completed; the gate throttles, it does not drop work.
        assert int(await redis.get(OBSERVED_KEY)) == 0

    @pytest.mark.asyncio
    async def test_a_single_process_cannot_exceed_the_cap_either(self, gate: LLMGate) -> None:
        # The weak version of the property. Kept because it isolates the Lua
        # from the multiprocessing machinery when the headline test goes red.
        slots = [await gate.try_acquire() for _ in range(ZAI_MAX_CONCURRENCY)]
        assert all(slot is not None for slot in slots)

        assert await gate.try_acquire() is None
        assert await gate.in_flight() == ZAI_MAX_CONCURRENCY


class TestSlotLifecycle:
    """Acquire, release, renew — each keyed to the exact token."""

    @pytest.mark.asyncio
    async def test_releasing_frees_the_slot_for_the_next_caller(self, gate: LLMGate) -> None:
        slots = [await gate.try_acquire() for _ in range(ZAI_MAX_CONCURRENCY)]
        assert await gate.try_acquire() is None

        assert await gate.release(slots[0]) is True
        assert await gate.try_acquire() is not None

    @pytest.mark.asyncio
    async def test_fences_are_monotonic(self, gate: LLMGate) -> None:
        first = await gate.try_acquire()
        await gate.release(first)
        second = await gate.try_acquire()
        assert second.fence > first.fence

    @pytest.mark.asyncio
    async def test_a_stale_holder_cannot_release_the_slot_that_replaced_it(
        self, redis: Redis
    ) -> None:
        # The exact-token rule. A worker whose lease lapsed runs its finally
        # block eventually; if release were "drop any member", that cleanup
        # would evict a slot legitimately held by someone else.
        short = LLMGate(redis, key="test:stale", capacity=1, lease_seconds=1)
        stale = await short.try_acquire()

        await asyncio.sleep(1.1)
        successor = await short.try_acquire()  # reaps the lapsed member, takes the slot
        assert successor is not None

        assert await short.release(stale) is False  # its token is long gone
        assert await short.in_flight() == 1  # the successor still holds it

    @pytest.mark.asyncio
    async def test_renewal_keeps_a_live_call_from_being_reaped(self, redis: Redis) -> None:
        short = LLMGate(redis, key="test:renew", capacity=1, lease_seconds=2)
        slot = await short.try_acquire()

        await asyncio.sleep(1.2)
        assert await short.renew(slot) is True
        await asyncio.sleep(1.2)

        # Past the ORIGINAL 2s expiry: without the renewal the reaper would
        # have handed this slot away while the call was still in flight.
        assert await short.try_acquire() is None
        assert await short.in_flight() == 1

    @pytest.mark.asyncio
    async def test_renewing_a_reaped_slot_fails_instead_of_resurrecting_it(
        self, redis: Redis
    ) -> None:
        # An unguarded ZADD would re-insert the token and push the account to
        # capacity+1 — the caller must learn it lost the slot instead.
        short = LLMGate(redis, key="test:resurrect", capacity=1, lease_seconds=1)
        slot = await short.try_acquire()

        await asyncio.sleep(1.1)
        successor = await short.try_acquire()
        assert successor is not None

        assert await short.renew(slot) is False
        assert await short.in_flight() == 1  # not 2


class TestExpiryReaping:
    """A dead worker must not hold a slot forever."""

    @pytest.mark.asyncio
    async def test_an_abandoned_slot_is_reclaimed_after_its_lease(self, redis: Redis) -> None:
        # Simulates SIGKILL: the slot is taken and release never runs.
        short = LLMGate(redis, key="test:reap", capacity=1, lease_seconds=1)
        assert await short.try_acquire() is not None
        assert await short.try_acquire() is None

        await asyncio.sleep(1.1)

        assert await short.try_acquire() is not None


class TestBackpressure:
    """Bounded wait; failure becomes queued work, never an ungated call."""

    @pytest.mark.asyncio
    async def test_acquire_raises_rather_than_waiting_forever(self, gate: LLMGate) -> None:
        for _ in range(ZAI_MAX_CONCURRENCY):
            await gate.try_acquire()

        started = time.monotonic()
        with pytest.raises(LLMGateTimeoutError):
            await gate.acquire(wait_seconds=0.5)
        elapsed = time.monotonic() - started

        assert 0.5 <= elapsed < 3.0

    @pytest.mark.asyncio
    async def test_the_context_manager_releases_on_an_exception(self, gate: LLMGate) -> None:
        # A judge timeout must not leak a slot for a whole lease period.
        with pytest.raises(RuntimeError):
            async with gate.slot():
                assert await gate.in_flight() == 1
                raise RuntimeError("judge call blew up")

        assert await gate.in_flight() == 0

    @pytest.mark.asyncio
    async def test_the_context_manager_yields_a_usable_slot(self, gate: LLMGate) -> None:
        async with gate.slot() as slot:
            assert isinstance(slot, GateSlot)
            assert slot.fence >= 1
            assert slot.expires_at > time.time()
        assert await gate.in_flight() == 0


# -- the account boundary is the PROVIDER ------------------------------------
#
# One shared key made a Mistral call queue behind Z.AI's three slots and fail
# with `gate saturated: no slot on llm_gate:zai:platform`. The providers are
# separate accounts with separate rate limits; total platform throughput was
# capped at three in flight across all of them, and the wrong one starved.


def test_each_provider_gets_its_own_key_and_capacity():
    """Different providers, different keys — and z.ai keeps its measured 3.

    MUTATION: make provider_account_key return one constant. The first
    assertion goes red.
    """
    assert provider_account_key("mistral") != provider_account_key("zai")
    assert provider_capacity("zai") == ZAI_MAX_CONCURRENCY == 3
    assert provider_capacity("mistral") == DEFAULT_MAX_CONCURRENCY
    # The z.ai key is byte-identical to the historical constant, so slots held
    # across a deploy stay accounted to the same set.
    assert provider_account_key("zai") == ZAI_ACCOUNT_KEY == "llm_gate:zai:platform"


def test_llm7_capacity_matches_its_measured_rate_limit():
    """llm7's keyless rate limit is ~1 req/8s (measured live) — DEFAULT_MAX_CONCURRENCY
    (3) would admit 3 concurrent calls to an account that answers 200-shaped
    rate-limit errors well before that (review finding 4)."""
    assert provider_capacity("llm7") == 1


@pytest.mark.asyncio
async def test_a_full_provider_does_not_block_a_different_one(redis: Redis):
    """The production symptom, reproduced: saturate z.ai, then call mistral.

    MUTATION: have for_provider ignore its argument and return self. The mistral
    acquire then finds z.ai's set full and raises LLMGateTimeoutError, which is
    exactly the log line this fix came from.
    """
    base = LLMGate(redis, lease_seconds=30)
    zai = base.for_provider("zai")
    held = [await zai.acquire(wait_seconds=1.0) for _ in range(ZAI_MAX_CONCURRENCY)]
    assert await zai.try_acquire() is None, "z.ai is now full"

    mistral = base.for_provider("mistral")
    slot = await mistral.acquire(wait_seconds=1.0)
    assert slot is not None, "a different account must not queue behind z.ai"

    assert await zai.in_flight() == ZAI_MAX_CONCURRENCY
    assert await mistral.in_flight() == 1
    await mistral.release(slot)
    for s in held:
        await zai.release(s)


@pytest.mark.asyncio
async def test_an_unmeasured_provider_still_holds_a_cap(redis: Redis):
    """The default is conservative, not absent: the N+1th call is refused."""
    gate = LLMGate(redis, lease_seconds=30).for_provider("deepseek")
    held = [await gate.acquire(wait_seconds=1.0) for _ in range(DEFAULT_MAX_CONCURRENCY)]
    assert await gate.try_acquire() is None
    for s in held:
        await gate.release(s)


# -- minimum spacing between calls to one account ----------------------------


@pytest.mark.asyncio
async def test_llm7_spaces_consecutive_calls_apart(redis: Redis):
    """llm7's keyless limit is ~1 req/8s and is per-ACCOUNT, not per model.

    Concurrency 1 bounds how many calls are in flight; it does NOT bound how
    fast they follow one another, and the gate releases its slot the instant a
    call returns. So the judge panel's fallback loop tried DeepSeek-V4, took a
    429, and tried gemini-3.1-flash-lite IN THE SAME SECOND — 22:27:51 twice in
    production, both 429, quorum lost.

    MUTATION: drop the pace wait from acquire() and the elapsed gap collapses to
    ~0, which is exactly the burst that produced the 429s.
    """
    gate = LLMGate(redis, lease_seconds=30).for_provider("llm7", min_interval_seconds=0.4)

    first = await gate.acquire(wait_seconds=5.0)
    await gate.release(first)

    started = time.monotonic()
    second = await gate.acquire(wait_seconds=5.0)
    elapsed = time.monotonic() - started
    await gate.release(second)

    assert elapsed >= 0.35, (
        f"consecutive llm7 calls were {elapsed:.3f}s apart — the account is "
        "rate-limited per ACCOUNT, so back-to-back calls 429 each other"
    )


@pytest.mark.asyncio
async def test_pacing_is_per_account_not_global(redis: Redis):
    """Two PACED providers must not share one queue of departure slots.

    Each provider is a separate account with a separate rate limit, so llm7's
    spacing must not delay a different paced account. Both gates are paced here
    deliberately: an unpaced gate returns before it ever reads a pace key, so it
    could not detect a mis-keyed marker at all.

    MUTATION: key the marker on a constant instead of the account
    (`self._pace_key = "llm_gate:global:pace"`) and this goes red — the second
    provider inherits the first's queue and waits an interval it does not owe.
    """
    base = LLMGate(redis, lease_seconds=30)
    llm7 = base.for_provider("llm7", min_interval_seconds=2.0)
    other = base.for_provider("deepseek", min_interval_seconds=2.0)

    # Two llm7 departures park ITS marker a full interval in the future.
    await llm7.release(await llm7.acquire(wait_seconds=5.0))
    await llm7.release(await llm7.acquire(wait_seconds=5.0))

    # deepseek's own marker is untouched, so its first departure is immediate.
    started = time.monotonic()
    slot = await other.acquire(wait_seconds=5.0)
    elapsed = time.monotonic() - started
    await other.release(slot)

    assert elapsed < 0.5, (
        f"a different account waited {elapsed:.3f}s behind llm7's pace queue"
    )


@pytest.mark.asyncio
async def test_an_unpaced_provider_keeps_its_previous_speed(redis: Redis):
    """Pacing is opt-in per provider: zai's measured 3-concurrent behaviour is
    unchanged, so this fix cannot ship a throughput cut to a healthy account.
    """
    gate = LLMGate(redis, lease_seconds=30).for_provider("zai")
    started = time.monotonic()
    for _ in range(3):
        await gate.release(await gate.acquire(wait_seconds=5.0))
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_the_last_bounded_out_claimant_returns_its_departure(redis: Redis):
    """A caller that gives up returns the departure it claimed, so the marker
    does not walk forward while the account is idle.

    Scoped to what the rollback can actually guarantee. It is a
    compare-and-set on the exact value written, so it only rewinds the LAST
    claim: an earlier claimant finds the marker already moved past its own value
    and correctly declines to rewind, because rewinding would discard a claim a
    later caller is still sleeping on. Under CONCURRENT claimants the marker can
    therefore retain the intermediate claims — bounded by how many callers fit
    the account's capacity, not unbounded.

    On the paced provider in production (llm7, capacity 1) that residue cannot
    arise at all: pacing runs while HOLDING the concurrency slot, so claims are
    serialised and this rollback is the only one in play.

    MUTATION: delete the _PACE_ROLLBACK_LUA call in _await_pace and the marker
    keeps the abandoned departure, so the assertion below goes red.
    """
    interval = 2.0
    gate = LLMGate(redis, lease_seconds=30).for_provider(
        "deepseek", min_interval_seconds=interval
    )
    # First caller departs now and arms the marker one interval ahead.
    await gate.release(await gate.acquire(wait_seconds=5.0))
    armed = float(await redis.get(f"{gate._key}:pace"))

    # Second caller cannot absorb the interval, so it must give the claim back.
    with pytest.raises(LLMGateTimeoutError):
        await gate.acquire(wait_seconds=0.2)

    after = float(await redis.get(f"{gate._key}:pace"))
    assert after == pytest.approx(armed, abs=0.01), (
        f"marker moved {after - armed:.1f}s on a caller that never departed"
    )


@pytest.mark.asyncio
async def test_the_real_llm7_interval_is_what_production_uses(redis: Redis):
    """Pinned to the production figure, not a test-only value: the other pacing
    tests shrink the interval for speed, so nothing else would notice the table
    being edited."""
    assert PROVIDER_MIN_INTERVAL_SECONDS["llm7"] == 8.0
    gate = LLMGate(redis, lease_seconds=30).for_provider("llm7")
    await gate.release(await gate.acquire(wait_seconds=1.0))
    started = time.monotonic()
    with pytest.raises(LLMGateTimeoutError):
        await gate.acquire(wait_seconds=1.0)
    assert time.monotonic() - started < 1.5, "a bounded-out caller slept anyway"


@pytest.mark.asyncio
async def test_a_bounded_out_caller_leaves_no_slot_held(redis: Redis):
    """Pacing runs while HOLDING the concurrency slot, so the timeout path must
    release it — otherwise one bounded-out caller parks a capacity-1 account.

    MUTATION: drop the `await self.release(slot)` in acquire's except branch and
    in_flight stays 1 with nobody running.
    """
    gate = LLMGate(redis, lease_seconds=30).for_provider("llm7")
    with pytest.raises(LLMGateTimeoutError):
        # First call departs immediately and arms the marker; second cannot fit.
        await gate.release(await gate.acquire(wait_seconds=1.0))
        await gate.acquire(wait_seconds=1.0)
    assert await gate.in_flight() == 0, "a bounded-out caller kept its slot"
