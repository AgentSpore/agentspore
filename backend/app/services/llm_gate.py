"""Account-wide concurrency gate for platform-owned LLM calls.

The constraint this exists for is a physical one. Since the geo-block pushed the
platform onto z.ai, ``glm-4.5-flash`` is our only reliably free model, and that
account tolerates roughly THREE concurrent requests. Exceed it and the provider
answers 429 for everyone — including calls that were already in flight.

Why a leased semaphore and not the obvious alternatives:

* **Not a token bucket.** A bucket caps a RATE (N per second). Our limit is on
  requests *simultaneously in flight*, which a bucket cannot express: three
  60-second calls started one per minute never violate any rate limit and still
  pin the account at its ceiling.
* **Not ``asyncio.Semaphore``.** It is per-PROCESS. The backend runs multiple
  workers against ONE z.ai account, so a local semaphore caps each worker at 3
  and the account at 3xN. It would make the limit look enforced while the
  provider still sees the overload — which is worse than no gate, because it
  reads as a control in code review.
* **Not in agent-runner.** Fighters spend their OWNER's key and need no gate;
  the judge spends OURS. Putting the gate on the fighter path would mix two
  trust and credential domains and still leave the judge ungated.

Therefore: one Redis sorted set per account, shared by every process.

**The rule that makes the number true.** EVERY backend call on the platform
z.ai account must pass through this gate — judges AND task generation AND any
adapter added later. The cap is a property of the ACCOUNT, not of the judge: if
one call site skips the gate, "at most 3 concurrent" is simply false, and no
amount of correctness inside this module recovers it.

The sorted set holds one member per in-flight call, scored by its lease expiry.
Expiry is what makes the gate self-healing: a worker that is SIGKILLed mid-call
cannot run its release, so its slot must free itself. Every acquire first drops
members whose lease has lapsed. This is also why the lease must OUTLAST the
HTTP hard timeout — if it did not, a slow-but-alive call would have its slot
reaped and handed to a fourth caller while the third is still talking to z.ai.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast

from loguru import logger
from redis.asyncio import Redis

# Concurrent in-flight requests tolerated by ONE platform z.ai account. Measured,
# not chosen: at 4 the provider starts answering 429 (observed 4 parallel -> 1x200,
# 2x500 during the 2026-07-15 migration).
ZAI_MAX_CONCURRENCY = 3

# The single account every platform-owned call shares. A per-caller key would
# defeat the entire purpose, so the key is deliberately not parameterised by
# caller — only by account.
ZAI_ACCOUNT_KEY = "llm_gate:zai:platform"

# How long a slot survives without renewal. MUST exceed the judge's hard HTTP
# timeout (JUDGE_HTTP_TIMEOUT_SECONDS in battle_judges.py), or a live call loses
# its slot to the reaper while still in flight. Renewal keeps genuinely long
# calls alive; this is only the ceiling for a caller that died.
DEFAULT_LEASE_SECONDS = 90

# Bounded wait: a caller that cannot get a slot within this window gives up and
# the work becomes a durable queued job (a battle_judge_run row stays 'pending'
# and the next reconciler pass reclaims it). An unbounded wait would pile up
# coroutines that outlive the row lease they were doing work for.
DEFAULT_WAIT_SECONDS = 20.0

# Poll interval bounds for the retry loop. Jittered because synchronised
# retries from N workers rediscover the same contention in lockstep.
_RETRY_MIN_SECONDS = 0.05
_RETRY_MAX_SECONDS = 0.4

# Acquire: reap expired members, then admit only if there is room.
#
# Atomicity is the entire point. Read-ZCARD-then-ZADD from Python is a
# check-then-act race: N workers all read 2, all decide there is room, and all
# add — the set ends at 2+N and the cap silently never held. In Lua the reap,
# the count and the insert are one indivisible step on a single-threaded server.
#
# KEYS[1] = the account's sorted set, KEYS[2] = the fence counter
# ARGV[1] = now, ARGV[2] = expiry, ARGV[3] = capacity, ARGV[4] = token
# Returns the fence number, or 0 when the account is full.
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[3]) then
    local fence = redis.call('INCR', KEYS[2])
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
    return fence
end
return 0
"""

# Release: drop THIS token only.
#
# ZREM by exact token, never ZPOPMIN or "remove one member": a worker whose
# lease already lapsed and was reaped must not, on finally-block cleanup,
# evict the slot of the caller that legitimately replaced it. Returns 1 if the
# token was still ours, 0 if it had already been reaped.
_RELEASE_LUA = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

# Renew: extend THIS token's lease, but only while we still hold it.
#
# ZSCORE-guarded rather than a bare ZADD: an unguarded ZADD would RE-INSERT a
# token that the reaper already removed, resurrecting a slot the account has
# since given to someone else and pushing it to capacity+1.
_RENEW_LUA = """
if redis.call('ZSCORE', KEYS[1], ARGV[2]) then
    redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])
    return 1
end
return 0
"""


class LLMGateTimeoutError(Exception):
    """No slot became free within the bounded wait.

    Not an error condition so much as backpressure. The caller must turn this
    into durable queued work — never into a retry loop that outlives its row
    lease, and never into a call made anyway.
    """


@dataclass(frozen=True)
class GateSlot:
    """One acquired slot on the account.

    ``fence`` is a monotonic number from INCR. It is not used to gate Redis
    itself (the token does that) but is carried so a caller can stamp downstream
    writes and recognise a result produced by an older slot for the same work.
    """

    token: str
    fence: int
    expires_at: float


class LLMGate:
    """Redis leased semaphore over one platform LLM account.

    Use it as an async context manager; the slot is released on the way out
    whatever happens, and re-entering is a fresh slot::

        async with LLMGate(redis).slot():
            await call_zai(...)

    Every method takes the exact token, so a stale holder can neither release
    nor renew a slot that has moved on.
    """

    def __init__(
        self,
        redis: Redis,
        key: str = ZAI_ACCOUNT_KEY,
        capacity: int = ZAI_MAX_CONCURRENCY,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._redis = redis
        self._key = key
        self._fence_key = f"{key}:fence"
        self._capacity = capacity
        self._lease_seconds = lease_seconds

    async def _eval(self, script: str, numkeys: int, *args: str) -> Any:
        """Run a Lua script, narrowing redis-py's sync/async union.

        ``redis.asyncio``'s ``eval`` is typed ``Awaitable[T] | T`` because the
        stubs are shared with the sync client. On an async client it is always
        the awaitable branch, so the cast states a fact rather than hiding a
        doubt — and it keeps the cast in ONE place instead of at every call.
        """
        return await cast("Awaitable[Any]", self._redis.eval(script, numkeys, *args))

    async def try_acquire(self) -> GateSlot | None:
        """One non-blocking attempt. None = the account is at capacity."""
        now = time.time()
        expires_at = now + self._lease_seconds
        token = secrets.token_hex(16)

        fence = await self._eval(
            _ACQUIRE_LUA,
            2,
            self._key,
            self._fence_key,
            str(now),
            str(expires_at),
            str(self._capacity),
            token,
        )
        if not fence:
            return None
        return GateSlot(token=token, fence=int(fence), expires_at=expires_at)

    async def acquire(self, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> GateSlot:
        """Wait, with jitter, for a slot. Raises LLMGateTimeoutError when bounded out.

        The jitter is not decoration: without it every worker that lost a race
        retries at the same instant and rediscovers the same contention, so the
        same caller can starve indefinitely while the account stays busy.
        """
        deadline = time.time() + wait_seconds
        while True:
            slot = await self.try_acquire()
            if slot is not None:
                return slot
            if time.time() >= deadline:
                raise LLMGateTimeoutError(
                    f"no slot on {self._key} within {wait_seconds}s (capacity {self._capacity})"
                )
            await asyncio.sleep(
                secrets.SystemRandom().uniform(_RETRY_MIN_SECONDS, _RETRY_MAX_SECONDS)
            )

    async def release(self, slot: GateSlot) -> bool:
        """Free this exact slot. False = it had already been reaped."""
        removed = await self._eval(_RELEASE_LUA, 1, self._key, slot.token)
        return bool(removed)

    async def renew(self, slot: GateSlot) -> bool:
        """Extend this exact slot's lease. False = we no longer hold it.

        A False here means the call in flight is now unsanctioned: the account
        has given our slot away. The caller should stop rather than press on.
        """
        renewed = await self._eval(
            _RENEW_LUA,
            1,
            self._key,
            str(time.time() + self._lease_seconds),
            slot.token,
        )
        return bool(renewed)

    async def in_flight(self) -> int:
        """Live slot count, excluding lapsed ones. Observability only.

        Never gate on this: by the time it returns, it is a fact about the past.
        The Lua acquire is the only safe place to make an admission decision.
        """
        await self._redis.zremrangebyscore(self._key, "-inf", time.time())
        return int(await self._redis.zcard(self._key))

    def slot(self, wait_seconds: float = DEFAULT_WAIT_SECONDS) -> _SlotContext:
        """Context manager that acquires on entry and always releases on exit."""
        return _SlotContext(self, wait_seconds)


class _SlotContext:
    """``async with`` wrapper around acquire/release.

    Release runs in ``__aexit__`` so an exception in the guarded call — a judge
    timeout, a cancellation — cannot leak a slot for a whole lease period. The
    lease expiry remains the backstop for the case no Python runs at all.
    """

    def __init__(self, gate: LLMGate, wait_seconds: float) -> None:
        self._gate = gate
        self._wait_seconds = wait_seconds
        self._slot: GateSlot | None = None

    async def __aenter__(self) -> GateSlot:
        self._slot = await self._gate.acquire(self._wait_seconds)
        return self._slot

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._slot is None:
            return
        try:
            await self._gate.release(self._slot)
        except Exception as release_error:
            # Never mask the original exception with a cleanup failure, and
            # never swallow it silently either: the lease expiry will reclaim
            # the slot, but a Redis that cannot release is worth an alert.
            logger.warning("llm_gate release failed for {}: {}", self._gate._key, release_error)
        finally:
            self._slot = None
