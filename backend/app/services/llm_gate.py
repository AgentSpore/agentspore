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
z.ai account must pass through this gate. The cap is a property of the ACCOUNT,
not of the judge: if one call site skips it, "at most 3 concurrent" is simply
false, and no amount of correctness inside this module recovers it.

As of this commit that rule HOLDS, verified rather than assumed. The only
``chat/completions`` POSTs in the backend are ``battle_judges.py`` (gated) and
``council_adapters.py:45``, and the latter targets ``openrouter.ai`` on the
OpenRouter account — a different account, not this one.
``openrouter_service.py`` never sends inference at all: it resolves provider
config (``resolve_provider``/``resolve_model``/``get_models``), and for z.ai
specifically it serves a ``static_models`` list, so it does not even call
``/models`` there. Hosted-agent inference leaves from agent-runner with the
OWNER's key, which is why the gate does not belong on that path.

So the judge is currently the sole backend consumer of this account. Any future
one — a task generator, a summariser, an eval — must acquire a slot here, or
this module's guarantee silently becomes a comment.

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

# Concurrency for a provider we have NOT measured. Equal to ZAI_MAX_CONCURRENCY
# so that a keying fix cannot ship a throughput CUT: moonshot carries both
# JUDGE_MODEL and DEMO_ANSWER_MODEL (kimi-k3) across four workers, so a lower
# default would move the busiest account onto a tighter cap than the mostly-idle
# one that was actually measured. A carried-over number, NOT an observation —
# replace it per provider once a real reading exists.
DEFAULT_MAX_CONCURRENCY = ZAI_MAX_CONCURRENCY

# Measured per-provider overrides.
PROVIDER_MAX_CONCURRENCY: dict[str, int] = {
    "zai": ZAI_MAX_CONCURRENCY,
    # llm7's keyless rate limit is ~1 req/8s (measured live, provider_health.py
    # module docstring / battle_judges.py error_shaped_200). DEFAULT_MAX_CONCURRENCY
    # (3) would admit 3 concurrent calls to an account this slow — a single
    # in-flight slot is the honest cap, not a carried-over guess.
    "llm7": 1,
}


# Minimum seconds between two DEPARTURES on one account. 8.0 for llm7 is the
# provider's keyless figure (~1 req/8s, per-ACCOUNT, shared by all its seats).
#
# The budget it fits (all read, none assumed): DEFAULT_WAIT_SECONDS 20s bounds
# ONE acquire and is the binding limit; JUDGE_HTTP_TIMEOUT_SECONDS 85s is per
# call and unaffected by waits BETWEEN calls; BATTLE_LEASE_SECONDS 300s is
# renewed after every half. A panel is REPLICATE_COUNT(3) x PRESENTED_ORDERS(2)
# = 6 SEQUENTIAL calls, so waits do not stack: each acquire waits at most one
# interval (8 < 20) and the panel adds ~48s against that renewed 300s lease.
# INVARIANT(llm7-pace): were the panel made CONCURRENT, the Nth claimant would
# wait N*8s and blow the 20s ceiling — raise pace and concurrency together.
PROVIDER_MIN_INTERVAL_SECONDS: dict[str, float] = {
    "llm7": 8.0,
}

# How long an account's pace marker outlives its last departure. Only needs to
# span one interval; the TTL exists so an idle provider's key does not persist.
_PACE_KEY_TTL_SECONDS = 60


def _as_text(value: Any) -> str:
    """Lua strings arrive as bytes from redis-py; compare and resend as text."""
    return value.decode() if isinstance(value, bytes) else str(value)


def provider_min_interval(provider: str) -> float:
    """Seconds an account requires between departures. 0.0 = unpaced."""
    return PROVIDER_MIN_INTERVAL_SECONDS.get(provider.strip().lower(), 0.0)


def provider_account_key(provider: str) -> str:
    """The gate key for one PROVIDER — which is the account boundary.

    A per-caller key would defeat the gate's purpose, so the key is still not
    parameterised by caller. It must be parameterised by provider: each provider
    is a separate account with a separate rate limit, and sharing one key made a
    Mistral call wait on Z.AI's three slots and then fail with
    ``gate saturated: no slot on llm_gate:zai:platform`` — total platform
    throughput capped at three in flight across four accounts, with the wrong
    provider starved.

    z.ai's key is byte-identical to the historical constant, so slots held
    across a deploy stay accounted to the same set.

    NORMALISED here as a second line of defence: a stray space or capital
    (" Zai/glm-4.5") would otherwise mint a SECOND key for one account — two slot
    pools, each under the cap, the account over it. The caller normalises with
    openrouter_service._provider_prefix, the helper its credential lookup uses.
    """
    return f"llm_gate:{provider.strip().lower()}:platform"


def provider_capacity(provider: str) -> int:
    """In-flight calls tolerated by one provider account."""
    return PROVIDER_MAX_CONCURRENCY.get(provider.strip().lower(), DEFAULT_MAX_CONCURRENCY)


ZAI_ACCOUNT_KEY = provider_account_key("zai")

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

# Pace: claim the next departure slot for an account, or report the wait.
#
# Concurrency and RATE are different limits and only the first had a mechanism
# here: capacity 1 admits a call the instant the previous one returns, which
# fired two llm7 429s inside one second (22:27:51).
#
# A claim, not a read-then-sleep: two workers that both READ "last departure"
# would compute the same instant and depart together, re-creating the burst
# across processes. INCR gives N callers N distinct slots.
#
# ``now`` comes from redis.call('TIME'), never from the caller: the marker is an
# absolute instant compared across workers, and each worker's own time.time()
# carries its host's clock skew. A worker 5s fast would push the marker 5s ahead
# of everyone else's frame; one 5s slow would read nxt < now and depart at once,
# collapsing the spacing. Redis is the one clock all workers already share.
#
# The TTL tracks the furthest promised departure rather than a flat constant: a
# deep queue can push the VALUE minutes ahead, and a key that expired while the
# instant it held was still in the future would let the next arrival read 0 and
# depart alongside callers still sleeping on earlier claims — the same-second
# burst this exists to prevent.
#
# KEYS[1] = pace key. ARGV = min interval, minimum ttl.
# Returns milliseconds to wait before departing (0 = go now).
_PACE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local interval = tonumber(ARGV[1])
local nxt = tonumber(redis.call('GET', KEYS[1]) or '0')
if nxt < now then nxt = now end
local departs_at = nxt + interval
local ttl = math.ceil(departs_at - now) + tonumber(ARGV[2])
redis.call('SET', KEYS[1], departs_at, 'EX', ttl)
-- wait_ms, the value written (to roll back), the value it replaced (to restore)
return {math.floor((nxt - now) * 1000),
        tostring(redis.call('GET', KEYS[1])),
        tostring(nxt)}
"""

# Roll back a claim this caller will not use: only if the marker is still the
# exact value we wrote, so a later claimant that already moved it past ours is
# never rewound. Without this, every caller that times out leaves its departure
# consumed and ratchets the marker further forward while the account sits idle.
#
# KEYS[1] = pace key. ARGV = the value we wrote, the value to restore, ttl.
_PACE_ROLLBACK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', tonumber(ARGV[3]))
    return 1
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
        min_interval_seconds: float = 0.0,
    ) -> None:
        self._redis = redis
        self._key = key
        self._fence_key = f"{key}:fence"
        self._pace_key = f"{key}:pace"
        self._capacity = capacity
        self._lease_seconds = lease_seconds
        self._min_interval = min_interval_seconds

    def for_provider(
        self,
        provider: str,
        lease_seconds: int | None = None,
        min_interval_seconds: float | None = None,
    ) -> LLMGate:
        """A gate over ONE provider's account, sharing this Redis connection.

        Callers hold a single gate instance built at startup; the provider is
        only known per call, so scoping happens here rather than at construction.
        Cheap by design — no I/O, just a rebind of key and capacity.

        ``lease_seconds`` exists because this module's rule — the lease MUST
        outlast the caller's HTTP timeout — is a property of the CALLER. The
        default 90 was sized for a judge verdict; the answer path holds a slot for
        180s, so inheriting 90 let the reaper free a live call's slot.

        ``min_interval_seconds`` overrides the provider's measured pace, for a
        caller whose own deadline cannot absorb it (see PROVIDER_MIN_INTERVAL).
        """
        interval = (
            provider_min_interval(provider)
            if min_interval_seconds is None
            else min_interval_seconds
        )
        return LLMGate(
            self._redis,
            key=provider_account_key(provider),
            capacity=provider_capacity(provider),
            lease_seconds=lease_seconds or self._lease_seconds,
            min_interval_seconds=interval,
        )

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
                # Pace AFTER the slot, not before: a caller that paced first
                # could burn its whole budget waiting for a departure and then
                # still fail to get a slot, having consumed a departure nobody
                # used. Holding the slot while pacing also makes the interval do
                # double duty as the account's real spacing — nothing else can
                # depart on a capacity-1 account while we hold it.
                try:
                    await self._await_pace(deadline)
                except LLMGateTimeoutError:
                    await self.release(slot)
                    raise
                return slot
            if time.time() >= deadline:
                raise LLMGateTimeoutError(
                    f"no slot on {self._key} within {wait_seconds}s (capacity {self._capacity})"
                )
            await asyncio.sleep(
                secrets.SystemRandom().uniform(_RETRY_MIN_SECONDS, _RETRY_MAX_SECONDS)
            )

    async def _await_pace(self, deadline: float) -> None:
        """Hold until this account's next departure slot. No-op when unpaced.

        Claims the slot BEFORE waiting on it, so two workers get two different
        departure times instead of both reading the same one.

        A claim past ``deadline`` is rolled back and raises: the bounded wait is
        what stops a coroutine outliving the row lease it works for. The
        rollback is what keeps repeated timeouts from ratcheting the marker ever
        further forward while the account sits idle — each caller that will not
        depart returns the departure it claimed.
        """
        if self._min_interval <= 0:
            return
        wait_ms, claimed, previous = await self._eval(
            _PACE_LUA,
            1,
            self._pace_key,
            str(self._min_interval),
            str(_PACE_KEY_TTL_SECONDS),
        )
        wait_seconds = int(wait_ms) / 1000.0
        if wait_seconds <= 0:
            return
        if time.time() + wait_seconds > deadline:
            await self._eval(
                _PACE_ROLLBACK_LUA,
                1,
                self._pace_key,
                _as_text(claimed),
                _as_text(previous),
                str(_PACE_KEY_TTL_SECONDS),
            )
            raise LLMGateTimeoutError(
                f"pace wait {wait_seconds:.1f}s on {self._key} exceeds the "
                f"caller's remaining budget (min interval {self._min_interval}s)"
            )
        await asyncio.sleep(wait_seconds)

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
