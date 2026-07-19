"""BattleJudgeBudgetService — the authoritative judge-call spend ledger (V68 B).

PostgreSQL is the budget, never Redis: an eviction or restart must not reopen
spend. Every provider HTTP attempt reserves one "call unit" in a SHORT,
INDEPENDENT transaction that commits BEFORE the request goes out — so a crash
after reservation still consumes the unit (billing status is unknown), and a
retry reserves a fresh unit rather than silently re-spending. The per-battle
12-attempt PRODUCT cap (halves x retries x reclaims) is enforced here by
counting ledger rows for the battle, so no combination of the operational
retry/reclaim ceilings can authorize a 13th provider request.

The reservation transaction is deliberately NOT part of the long-lived
settlement transaction: holding counter-row locks across a 60s HTTP call would
serialize the platform and risk deadlocks. It runs on its own session, validates
BOTH live lease tokens immediately before incrementing, and commits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.redis_client import get_redis

# Advisory-lock namespace for the daily counters, distinct from the challenge and
# rating namespaces so a budget lock can never collide with them.
BUDGET_LOCK_NAMESPACE = 0x62_74_6C_34  # "btl4"


def current_budget_day() -> date:
    """The ONE decision of "what day is the judge budget on" — process-local.

    Every layer that touches ``budget_day`` (the authoritative reservation
    writers here, the advisory accept-preflight read in the repository) must
    call this and pass the result down. A SQL-side ``CURRENT_DATE`` is a second,
    independent source: it resolves in the DATABASE timezone, so with a process
    running MSK against a UTC database the preflight read a different day than
    the writers for ~3h daily and silently fell open.

    Process-local (not UTC) is the canonical choice because every existing
    ``budget_day`` row was written with ``date.today()``; switching to a UTC day
    would reinterpret stored history and shift a live budget window mid-day.
    """
    return date.today()

# Redis keys for the transient circuit breaker (V68 B5). Redis is the RIGHT store
# here (unlike the spend ledger): the breaker is transient by design, so an
# eviction only closes it early, which fails safe toward judging.
_BREAKER_KEY = "battle:judge:breaker"
_FAILURES_KEY = "battle:judge:failures"
_ATTEMPTS_KEY = "battle:judge:attempts"
# A fixed sub-key for the single global counter (owners hash to their own keys).
_GLOBAL_LOCK_KEY = 0

# Reasons that map to battles.judging_stop_reason and must terminally settle the
# battle UNRATED — the budget for THIS period is spent, so waiting changes
# nothing. STALE_LEASE is NOT one of these: it means this worker lost its lease
# and simply has no right to spend, exactly like the existing "lost the row" path.
STOP_REASONS = frozenset(
    {"owner_budget_exhausted", "global_budget_exhausted", "battle_attempt_cap"}
)
STALE_LEASE = "stale_lease"


class JudgeBreakerOpen(Exception):  # noqa: N818 - spec-named, not an *Error*
    """Raised when the judge circuit breaker is open (V68 B5).

    A TRANSIENT platform/provider incident, NOT a budget stop: the caller must
    leave the battle in 'judging' for a later pass (do not settle, do not run the
    stranded escape hatch), because the panel can still complete once the breaker
    closes.
    """


async def breaker_is_open() -> bool:
    """Is the judge breaker open? Redis-absent-safe: absent/error => CLOSED.

    Failing closed (returning False on a Redis error) is deliberate: the breaker
    is only a defence-in-depth throttle on top of the authoritative Postgres
    budget, so a Redis outage must not itself freeze judging.
    """
    try:
        redis = await get_redis()
        return bool(await redis.exists(_BREAKER_KEY))
    except Exception as exc:  # noqa: BLE001 - breaker is best-effort
        logger.debug("judge breaker check unavailable (treating as closed): {}", exc)
        return False


async def _open_breaker(redis) -> None:
    await redis.set(_BREAKER_KEY, "1", ex=get_settings().battle_breaker_ttl_seconds)


async def breaker_record_attempt() -> None:
    """Count one provider attempt; open the breaker on a per-minute spike."""
    settings = get_settings()
    try:
        redis = await get_redis()
        bucket = int(time.time()) // settings.battle_breaker_spike_window_seconds
        key = f"{_ATTEMPTS_KEY}:{bucket}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.battle_breaker_spike_window_seconds)
        if count >= settings.battle_breaker_spike_threshold:
            await _open_breaker(redis)
    except Exception as exc:  # noqa: BLE001 - breaker is best-effort
        logger.debug("judge breaker attempt-record unavailable: {}", exc)


async def breaker_record_failure(*, permanent: bool) -> None:
    """Count one provider failure; open the breaker on threshold or immediately.

    A ``permanent`` (balance/auth) failure opens the breaker at once — no backoff
    creates money — whereas transient failures only open it once enough occur in
    the window.
    """
    settings = get_settings()
    try:
        redis = await get_redis()
        if permanent:
            await _open_breaker(redis)
            return
        bucket = int(time.time()) // settings.battle_breaker_failure_window_seconds
        key = f"{_FAILURES_KEY}:{bucket}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.battle_breaker_failure_window_seconds)
        if count >= settings.battle_breaker_failure_threshold:
            await _open_breaker(redis)
    except Exception as exc:  # noqa: BLE001 - breaker is best-effort
        logger.debug("judge breaker failure-record unavailable: {}", exc)


class JudgeBudgetExhausted(Exception):  # noqa: N818 - spec-named, not an *Error*
    """Raised when a reservation is refused for a terminal budget reason.

    ``reason`` is one of :data:`STOP_REASONS` and becomes the battle's
    ``judging_stop_reason``. A stale-lease refusal does NOT raise this — the
    caller treats that as a lost row.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ReservationResult:
    """The outcome of one :meth:`BattleJudgeBudgetService.reserve_call`."""

    granted: bool
    reason: str | None = None
    ledger_id: str | None = None
    provider_attempt_no: int | None = None


class BattleJudgeBudgetService:
    """Reserve and settle judge-call units against the PostgreSQL ledger."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def reserve_call(
        self,
        *,
        battle_id: str,
        judge_run_id: str,
        battle_lease_token: str,
        run_lease_token: str,
        owner_a_user_id: str,
        owner_b_user_id: str,
        provider: str,
        model: str,
    ) -> ReservationResult:
        """Reserve ONE call unit in a short independent transaction, or refuse.

        Order (all before the provider request):
          1. lock the global + both owner daily counters (sorted, deadlock-free);
          2. UNDER those locks, confirm both the battle lease and the raw
             judge-run lease are still live and owned by the supplied tokens
             (F1: locking first closes the reclaim-while-blocked double-pay race);
          3. refuse at the 12-row per-battle cap, or at a global/owner limit;
          4. otherwise increment each counter once and insert the ledger row;
          5. commit — only THEN may the caller transmit.
        """
        settings = get_settings()
        budget_day = current_budget_day()
        # Distinct owners are mandatory for a rated (paid) panel, but dedupe
        # defensively so a same-owner battle can never double-charge one owner.
        owners = sorted({str(owner_a_user_id), str(owner_b_user_id)})

        async with self._session_factory() as session, session.begin():
            # Serialise the counters this reservation touches BEFORE validating the
            # leases (F1). If the lease check ran first, a worker could pass it,
            # then block on the advisory lock while BOTH leases expire and another
            # worker reclaims the battle+run — the blocked worker would then wake,
            # take the lock, and insert a reservation + transmit with a now-stale
            # token: a second paid call for one logical half. Locking first means
            # the definitive lease check below runs under the lock, so nothing can
            # reclaim between the check and the insert.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :key)"),
                {"ns": BUDGET_LOCK_NAMESPACE, "key": _GLOBAL_LOCK_KEY},
            )
            for owner in owners:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:ns, hashtext(:owner))"),
                    {"ns": BUDGET_LOCK_NAMESPACE, "owner": owner},
                )

            live = await session.execute(
                text(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1 FROM battles b
                            WHERE b.id = CAST(:battle_id AS UUID)
                              AND b.status = 'judging'
                              AND b.lease_token = CAST(:battle_lease AS UUID)
                              AND b.lease_expires_at > NOW()
                        ) AS battle_ok,
                        EXISTS (
                            SELECT 1 FROM battle_judge_runs r
                            WHERE r.id = CAST(:run_id AS UUID)
                              -- The run must belong to THIS battle, or a run from a
                              -- different battle could authorize a spend here.
                              AND r.battle_id = CAST(:battle_id AS UUID)
                              AND r.status = 'running'
                              AND r.lease_token = CAST(:run_lease AS UUID)
                              AND r.lease_expires_at > NOW()
                        ) AS run_ok
                    """
                ),
                {
                    "battle_id": str(battle_id),
                    "battle_lease": str(battle_lease_token),
                    "run_id": str(judge_run_id),
                    "run_lease": str(run_lease_token),
                },
            )
            row = live.mappings().one()
            if not (row["battle_ok"] and row["run_ok"]):
                # Lost a lease (possibly while blocked on the lock above): no right
                # to spend. Not a budget stop.
                return ReservationResult(granted=False, reason=STALE_LEASE)

            # Per-battle product cap: count every ledger row this battle has
            # already reserved, across all halves, retries and reclaims.
            battle_calls = int(
                (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM battle_judge_call_ledger "
                            "WHERE battle_id = CAST(:battle_id AS UUID)"
                        ),
                        {"battle_id": str(battle_id)},
                    )
                ).scalar_one()
            )
            if battle_calls >= settings.battle_judge_max_attempts_per_battle:
                return ReservationResult(granted=False, reason="battle_attempt_cap")

            global_used = await self._counter_value(
                session, "battle_judge_global_daily_usage", budget_day, None
            )
            if global_used >= settings.battle_judge_global_daily_call_limit:
                return ReservationResult(granted=False, reason="global_budget_exhausted")

            for owner in owners:
                owner_used = await self._counter_value(
                    session, "battle_judge_owner_daily_usage", budget_day, owner
                )
                if owner_used >= settings.battle_judge_owner_daily_call_limit:
                    return ReservationResult(
                        granted=False, reason="owner_budget_exhausted"
                    )

            attempt_no = int(
                (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM battle_judge_call_ledger "
                            "WHERE judge_run_id = CAST(:run_id AS UUID)"
                        ),
                        {"run_id": str(judge_run_id)},
                    )
                ).scalar_one()
            ) + 1

            await session.execute(
                text(
                    """
                    INSERT INTO battle_judge_global_daily_usage
                        (budget_day, reserved_calls)
                    VALUES (:day, 1)
                    ON CONFLICT (budget_day)
                    DO UPDATE SET reserved_calls =
                        battle_judge_global_daily_usage.reserved_calls + 1
                    """
                ),
                {"day": budget_day},
            )
            for owner in owners:
                await session.execute(
                    text(
                        """
                        INSERT INTO battle_judge_owner_daily_usage
                            (budget_day, owner_user_id, reserved_calls)
                        VALUES (:day, CAST(:owner AS UUID), 1)
                        ON CONFLICT (budget_day, owner_user_id)
                        DO UPDATE SET reserved_calls =
                            battle_judge_owner_daily_usage.reserved_calls + 1
                        """
                    ),
                    {"day": budget_day, "owner": owner},
                )

            ledger_id = (
                await session.execute(
                    text(
                        """
                        INSERT INTO battle_judge_call_ledger
                            (battle_id, judge_run_id, owner_a_user_id,
                             owner_b_user_id, budget_day, provider_attempt_no,
                             provider, model, status)
                        VALUES (CAST(:battle_id AS UUID), CAST(:run_id AS UUID),
                                CAST(:owner_a AS UUID), CAST(:owner_b AS UUID),
                                :day, :attempt_no, :provider, :model, 'reserved')
                        RETURNING id
                        """
                    ),
                    {
                        "battle_id": str(battle_id),
                        "run_id": str(judge_run_id),
                        "owner_a": str(owner_a_user_id),
                        "owner_b": str(owner_b_user_id),
                        "day": budget_day,
                        "attempt_no": attempt_no,
                        "provider": provider,
                        "model": model,
                    },
                )
            ).scalar_one()

        return ReservationResult(
            granted=True, ledger_id=str(ledger_id), provider_attempt_no=attempt_no
        )

    async def reserve_validation_call(
        self,
        *,
        user_id: str,
        provider: str,
        model: str,
    ) -> ReservationResult:
        """Reserve ONE call unit for a task-validation LLM call, or refuse.

        The SAME budget as the judge panel, deliberately: same advisory-lock
        namespace, same ``battle_judge_global_daily_usage`` /
        ``battle_judge_owner_daily_usage`` counters, same ``STOP_REASONS``, same
        reserve-commit-then-transmit order. A second mechanism with its own
        counters would mean the global daily cap is not a cap — judging could be
        stopped for the day while validation kept spending, which is precisely
        the hole a separate ledger creates.

        The submitter is charged as the owner: they are the one party to a
        validation call, so their ``battle_judge_owner_daily_call_limit`` is what
        bounds how much one account can make the platform spend, exactly as it
        bounds their share of a judge panel.

        Differences from :meth:`reserve_call`, all forced by there being no
        battle here: no lease validation (there is no row to lose — the caller
        holds a task row it just created, not a lease), no per-battle attempt
        cap, and the ledger row is written with ``kind='validation'`` and a
        ``submitter_user_id`` instead of the battle/run/owner-pair columns (the
        V70 ``battle_judge_call_kind_shape`` CHECK enforces exactly one of the
        two shapes). ``provider_attempt_no`` is always 1: validation is a single
        call with no retry ladder, so there is no attempt sequence to number.

        Refusals are never fatal to the submission — the caller leaves the task
        in 'pending_validation' for a later pass. A dropped submission would be
        worse than a delayed one.
        """
        settings = get_settings()
        budget_day = current_budget_day()
        owner = str(user_id)

        async with self._session_factory() as session, session.begin():
            # Same lock order as reserve_call — global first, then the owner —
            # so a judge reservation and a validation reservation running
            # concurrently can never deadlock against each other.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :key)"),
                {"ns": BUDGET_LOCK_NAMESPACE, "key": _GLOBAL_LOCK_KEY},
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:ns, hashtext(:owner))"),
                {"ns": BUDGET_LOCK_NAMESPACE, "owner": owner},
            )

            global_used = await self._counter_value(
                session, "battle_judge_global_daily_usage", budget_day, None
            )
            if global_used >= settings.battle_judge_global_daily_call_limit:
                return ReservationResult(granted=False, reason="global_budget_exhausted")

            owner_used = await self._counter_value(
                session, "battle_judge_owner_daily_usage", budget_day, owner
            )
            if owner_used >= settings.battle_judge_owner_daily_call_limit:
                return ReservationResult(granted=False, reason="owner_budget_exhausted")

            await session.execute(
                text(
                    """
                    INSERT INTO battle_judge_global_daily_usage
                        (budget_day, reserved_calls)
                    VALUES (:day, 1)
                    ON CONFLICT (budget_day)
                    DO UPDATE SET reserved_calls =
                        battle_judge_global_daily_usage.reserved_calls + 1
                    """
                ),
                {"day": budget_day},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO battle_judge_owner_daily_usage
                        (budget_day, owner_user_id, reserved_calls)
                    VALUES (:day, CAST(:owner AS UUID), 1)
                    ON CONFLICT (budget_day, owner_user_id)
                    DO UPDATE SET reserved_calls =
                        battle_judge_owner_daily_usage.reserved_calls + 1
                    """
                ),
                {"day": budget_day, "owner": owner},
            )

            ledger_id = (
                await session.execute(
                    text(
                        """
                        INSERT INTO battle_judge_call_ledger
                            (kind, submitter_user_id, budget_day,
                             provider_attempt_no, provider, model, status)
                        VALUES ('validation', CAST(:owner AS UUID), :day,
                                1, :provider, :model, 'reserved')
                        RETURNING id
                        """
                    ),
                    {
                        "owner": owner,
                        "day": budget_day,
                        "provider": provider,
                        "model": model,
                    },
                )
            ).scalar_one()

        return ReservationResult(
            granted=True, ledger_id=str(ledger_id), provider_attempt_no=1
        )

    async def settle_call(
        self,
        ledger_id: str,
        *,
        succeeded: bool,
        http_status: int | None = None,
        error_class: str | None = None,
    ) -> None:
        """Mark a reserved ledger row succeeded/failed. Never touches counters.

        The reservation already spent the unit; this only records the outcome for
        observability (sanitised status/error class only — never a provider body
        or credential). Best-effort: a failure to record must not undo a call
        that already happened.
        """
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE battle_judge_call_ledger
                        SET status = :status,
                            http_status = :http_status,
                            error_class = :error_class,
                            finished_at = NOW()
                        WHERE id = CAST(:ledger_id AS UUID)
                          AND status = 'reserved'
                        """
                    ),
                    {
                        "status": "succeeded" if succeeded else "failed",
                        "http_status": http_status,
                        "error_class": (error_class[:80] if error_class else None),
                        "ledger_id": str(ledger_id),
                    },
                )
        except Exception as exc:  # noqa: BLE001 - observability write, never fatal
            logger.warning("failed to settle judge-call ledger {}: {}", ledger_id, exc)

    @staticmethod
    async def _counter_value(session, table: str, day: date, owner: str | None) -> int:
        """Read a daily counter's reserved_calls (0 if the row does not exist yet)."""
        if owner is None:
            result = await session.execute(
                text(
                    f"SELECT reserved_calls FROM {table} WHERE budget_day = :day"  # noqa: S608
                ),
                {"day": day},
            )
        else:
            result = await session.execute(
                text(
                    f"SELECT reserved_calls FROM {table} "  # noqa: S608
                    "WHERE budget_day = :day AND owner_user_id = CAST(:owner AS UUID)"
                ),
                {"day": day, "owner": owner},
            )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else 0
