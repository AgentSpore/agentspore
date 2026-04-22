"""Per-scope circuit breaker (OSS-lite).

Guards outbound calls so a failing dependency doesn't stampede. Scope
is a free-form string (``github``, ``openrouter:user-<id>``, ``jira``),
so the breaker works equally for shared infra (single OpenRouter key)
and per-user/per-integration resources.

State machine:
* **closed**: calls flow, failures count toward threshold
* **open**: calls short-circuit with :class:`CircuitOpenError` until
  ``next_probe_at``
* **half_open**: one trial call allowed after cooldown; success →
  closed, failure → open again

State is persisted in ``circuit_breaker_state`` (V51) so the breaker
survives restarts and multi-worker uvicorn.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import AuthError, CircuitOpenError, UpstreamError

T = TypeVar("T")


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 5
    window_seconds: int = 60
    cooldown_seconds: int = 30

    @classmethod
    def default(cls) -> "CircuitBreakerPolicy":
        return cls()


class CircuitBreaker:
    """Usage::

        breaker = CircuitBreaker(db)
        result = await breaker.guard("github", lambda: gh.fetch_pr(...))
    """

    def __init__(
        self,
        db: AsyncSession,
        policy: CircuitBreakerPolicy | None = None,
    ):
        self.db = db
        self.policy = policy or CircuitBreakerPolicy.default()

    async def _load(self, scope: str) -> dict:
        result = await self.db.execute(
            text(
                """
                SELECT state, failure_count, last_failure_at, opened_at, next_probe_at
                  FROM circuit_breaker_state
                 WHERE scope_key = :k
                """
            ),
            {"k": scope},
        )
        row = result.mappings().first()
        if row:
            return dict(row)
        await self.db.execute(
            text(
                """
                INSERT INTO circuit_breaker_state (scope_key, state)
                VALUES (:k, 'closed')
                ON CONFLICT (scope_key) DO NOTHING
                """
            ),
            {"k": scope},
        )
        await self.db.commit()
        return {
            "state": "closed",
            "failure_count": 0,
            "last_failure_at": None,
            "opened_at": None,
            "next_probe_at": None,
        }

    async def guard(
        self,
        scope: str,
        call: Callable[[], Awaitable[T]],
    ) -> T:
        """Run ``call`` under breaker supervision for ``scope``."""
        state = await self._load(scope)

        if state["state"] == "open":
            next_probe = state.get("next_probe_at")
            if next_probe and next_probe <= datetime.now(timezone.utc):
                await self._set_half_open(scope)
            else:
                raise CircuitOpenError(
                    f"upstream temporarily unavailable (circuit open for {scope})"
                )

        try:
            result = await call()
        except AuthError:
            raise
        except UpstreamError:
            await self._record_failure(scope)
            raise
        except Exception as exc:
            logger.exception("guarded call raised unclassified error; counting as upstream")
            await self._record_failure(scope)
            raise UpstreamError(str(exc)) from exc
        else:
            await self._record_success(scope)
            return result

    async def _record_success(self, scope: str) -> None:
        await self.db.execute(
            text(
                """
                UPDATE circuit_breaker_state
                   SET state = 'closed',
                       failure_count = 0,
                       opened_at = NULL,
                       next_probe_at = NULL,
                       updated_at = now()
                 WHERE scope_key = :k
                """
            ),
            {"k": scope},
        )
        await self.db.commit()

    async def _record_failure(self, scope: str) -> None:
        state = await self._load(scope)
        new_count = (state.get("failure_count") or 0) + 1
        should_open = new_count >= self.policy.failure_threshold

        if should_open:
            await self.db.execute(
                text(
                    """
                    UPDATE circuit_breaker_state
                       SET state = 'open',
                           failure_count = :c,
                           last_failure_at = now(),
                           opened_at = now(),
                           next_probe_at = now() + make_interval(secs => :cd),
                           updated_at = now()
                     WHERE scope_key = :k
                    """
                ),
                {"k": scope, "c": new_count, "cd": self.policy.cooldown_seconds},
            )
            logger.warning(
                "circuit breaker OPEN for {}: {} failures in {}s window",
                scope, new_count, self.policy.window_seconds,
            )
        else:
            await self.db.execute(
                text(
                    """
                    UPDATE circuit_breaker_state
                       SET failure_count = :c,
                           last_failure_at = now(),
                           updated_at = now()
                     WHERE scope_key = :k
                    """
                ),
                {"k": scope, "c": new_count},
            )
        await self.db.commit()

    async def _set_half_open(self, scope: str) -> None:
        await self.db.execute(
            text(
                """
                UPDATE circuit_breaker_state
                   SET state = 'half_open',
                       updated_at = now()
                 WHERE scope_key = :k
                """
            ),
            {"k": scope},
        )
        await self.db.commit()
        logger.info("circuit breaker HALF_OPEN for {}: trial probe allowed", scope)
