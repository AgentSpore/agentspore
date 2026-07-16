"""BattleService — challenge, consent, reservation and readiness (step 8).

This layer exists to keep four facts apart that are constantly mistaken for
each other. Collapsing any pair of them is how an agent ends up spending its
owner's LLM budget on a battle nobody agreed to:

1. **Owner consent** — ``POST /battles/{id}/accept`` sets agent_b_accepted_at.
   A human decision, made over JWT, valid for as long as the challenge is. It
   does NOT require the agent to be reachable: an owner may accept while their
   agent is offline, and that is a legitimate, common case.
2. **Live handoff** — ``DeliveryResult.DELIVERED``. A claim about one instant
   of transport: "somebody was listening when we pushed". It is not persisted
   and proves nothing about whether the agent read, understood, or will act.
   QUEUED is an equally valid outcome and simply means the heartbeat drain owns
   the event now.
3. **Agent ACK** — ``agent_events.acked_at``. The target confirmed ONE specific
   event_id. ``mark_acked()`` never inspects the event type and never calls
   battle code, so a generic ACK says nothing about battles.
4. **Battle readiness** — the conclusion THIS service draws, and the only thing
   that admits a battle to 'queued': both exact battle_ready_check event ids of
   the CURRENT readiness generation are acked, by the right agents, inside the
   lease. Fact 3 is an input to fact 4, never a substitute for it.

The fake ``online`` signal is deliberately absent from this module. Both of the
platform's liveness hints are lies: ``last_heartbeat IS NOT NULL``
(councils.py:123) is true forever after an agent's first beat, and
``agent_repo.py:228`` only ever sets is_active = TRUE — nothing clears it. The
only current liveness the platform has is a fresh ready-ACK, which is exactly
what fact 4 measures.

Layering: this service owns the transaction boundary for the sequences below
and may call connection_manager; battle_repo imports nothing from here.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.battle_repo import (
    BattleRepository,
    ChallengeDenial,
    ReservationConflictError,
)
from app.services.connection_manager import DeliveryResult, dispatch_existing

# How long a challenge waits for B's owner to answer. Consent is a human
# decision, so this is generous — hours, not seconds.
CHALLENGE_TTL_SECONDS = 86_400

# Per-target admission cap: at most N challenges against ONE agent per window,
# counted from every challenger. Deliberately per-TARGET, not per-challenger:
# the budget a challenge spends belongs to the target's owner, so the limit
# that protects them must count what lands on them. A per-challenger limit
# (councils.py:72-89, 10/hour) caps nothing when 10 accounts each challenge the
# same agent once.
TARGET_CHALLENGE_CAP = 5
TARGET_CHALLENGE_WINDOW_SECONDS = 3_600

# Per-challenger rate limit, enforced fail-closed in Redis. This is the
# secondary gate — it bounds a single account's fan-out across MANY targets,
# which the per-target cap cannot see.
CHALLENGER_RATE_LIMIT = 20
CHALLENGER_RATE_WINDOW_SECONDS = 3_600

# After a decline, the same challenger must leave that target alone for a
# while. Without it "decline" is only advisory: the challenger re-sends
# immediately and the block list becomes the target's only real defence.
DECLINE_COOLDOWN_SECONDS = 86_400

# How long both fighters stay reserved while we wait for their ready-ACKs.
# Short on purpose: a reservation blocks the agent from every other battle, so
# an unanswered ping must not strand it.
READY_LEASE_SECONDS = 60
# Outlives the ready lease by a small margin only. The reservation must not
# lapse while readiness is still legitimately in flight, but it must not
# outlive it by much either.
RESERVATION_SECONDS = READY_LEASE_SECONDS + 30


class ChallengeDeniedError(Exception):
    """An admission gate refused. ``reason`` names which one."""

    def __init__(self, reason: ChallengeDenial):
        self.reason = reason
        super().__init__(reason.value)


class LimiterUnavailableError(Exception):
    """The challenge limiter could not be consulted, so nothing was created.

    Its own exception rather than a False return, because "the limiter is down"
    and "the limiter said no" deserve different answers (503 vs 429) and
    because a bare False is exactly the value a caller forgets to branch on.
    """


class BattleService:
    """Challenge, consent, reservation and readiness for battles."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = BattleRepository(db)
        self.events = AgentEventRepository(db)

    # -- admission ----------------------------------------------------------

    async def _check_challenger_rate_limit(self, agent_a_id: str) -> None:
        """Fail-CLOSED per-challenger rate limit. Raises, never returns False.

        The councils limiter (councils.py:72-89) swallows every Redis error and
        continues — acceptable there, because convening a council spends the
        platform's own free credits. It is not acceptable here. A challenge
        spends the TARGET owner's inference budget, so a limiter we cannot
        consult must deny: during a Redis outage the correct number of
        unmetered challenges against someone else's key is zero, not infinity.

        Deliberately not reusing _check_rate_limit: same primitive, inverted
        failure semantics. Importing it and hoping the caller remembers the
        difference is how the fail-open behaviour would quietly spread.
        """
        try:
            redis = await get_redis()
            key = f"battle:challenge:ratelimit:{agent_a_id}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, CHALLENGER_RATE_WINDOW_SECONDS)
        except Exception as exc:
            logger.warning("Challenge limiter unavailable for {}: {}", agent_a_id, exc)
            raise LimiterUnavailableError from exc
        if count > CHALLENGER_RATE_LIMIT:
            raise ChallengeDeniedError(ChallengeDenial.CHALLENGER_RATE_LIMITED)

    async def create_challenge(
        self,
        task_id: str,
        agent_a_id: str,
        challenger_owner_user_id: str,
        agent_b_id: str | None = None,
    ) -> str:
        """Create a challenge. Returns the battle id. Does not commit.

        ``agent_b_id=None`` opens the challenge to any eligible claimant.

        Order matters. The limiter runs BEFORE the insert, because a limiter
        that runs after has already let the row exist. The advisory lock runs
        before the insert too, so the per-target cap counts against a target
        nobody else is concurrently challenging.

        Raises ChallengeDeniedError (a rule refused) or LimiterUnavailableError (we could
        not tell). Both leave no battle row — the caller's transaction is
        rolled back on the exception path and, more fundamentally, the insert
        itself re-checks every rule and simply matches no rows.
        """
        await self._check_challenger_rate_limit(agent_a_id)

        target_owner: str | None = None
        if agent_b_id:
            await self.repo.lock_challenge_target(agent_b_id)
            target_owner = await self._require_owner_snapshot(agent_b_id)

        denial = await self.repo.diagnose_challenge(
            task_id=task_id,
            agent_a_id=agent_a_id,
            challenger_owner_user_id=challenger_owner_user_id,
            agent_b_id=agent_b_id,
            target_cap=TARGET_CHALLENGE_CAP,
            target_window_seconds=TARGET_CHALLENGE_WINDOW_SECONDS,
        )
        if denial is not None:
            raise ChallengeDeniedError(denial)

        battle_id = await self.repo.create_challenge(
            task_id=task_id,
            agent_a_id=agent_a_id,
            agent_a_owner_snapshot=challenger_owner_user_id,
            challenge_ttl_seconds=CHALLENGE_TTL_SECONDS,
            target_cap=TARGET_CHALLENGE_CAP,
            target_window_seconds=TARGET_CHALLENGE_WINDOW_SECONDS,
            agent_b_id=agent_b_id,
            agent_b_owner_snapshot=target_owner,
        )
        if battle_id is None:
            # The diagnostic said yes and the insert still refused: something
            # changed underneath us. Report the denial rather than a 500 — the
            # outcome (no row) is exactly what a denial means.
            logger.info(
                "Challenge insert refused after clean diagnosis: a={} b={}",
                agent_a_id, agent_b_id,
            )
            raise ChallengeDeniedError(ChallengeDenial.TARGET_CAPPED)
        return battle_id

    async def _require_owner_snapshot(self, agent_id: str) -> str | None:
        """Read the CURRENT owner of an agent, to be frozen into the battle.

        Returns None when the agent has no owner; the insert's eligibility
        predicate rejects that case, so this never invents a value to satisfy
        a NOT NULL.
        """
        return await AgentRepository(self.db).get_agent_owner_user_id(agent_id)

    async def claim_open_challenge(
        self, battle_id: str, agent_b_id: str, claiming_user_id: str
    ) -> dict | None:
        """Take an open challenge's empty B slot. Does not commit.

        Returns the claimed battle, or None if the slot is gone or any
        admission rule refuses. The two are deliberately indistinguishable to
        the caller: telling a claimant "you are blocked" would turn this into a
        way to read someone else's block list.

        The advisory lock is taken on the CLAIMANT, because the claimant is who
        the per-target cap protects here: an open challenge that lands on you
        spends YOUR owner's budget exactly like a named one, so it counts
        against your cap and must serialise against other challenges arriving
        at you.

        Claiming is not consent — B's owner still has to accept afterwards.
        """
        await self.repo.lock_challenge_target(agent_b_id)
        return await self.repo.claim_open_challenge_as_owner(
            battle_id=battle_id,
            agent_b_id=agent_b_id,
            claiming_user_id=claiming_user_id,
            target_cap=TARGET_CHALLENGE_CAP,
            target_window_seconds=TARGET_CHALLENGE_WINDOW_SECONDS,
        )

    # -- consent ------------------------------------------------------------

    async def accept(self, battle_id: str, accepting_user_id: str) -> dict | None:
        """Record B's owner consent. Does not commit. None = not acceptable.

        Consent only. It does not reserve, ping, or start anything, and it
        explicitly does not require the agent to be online — see fact 1 in the
        module docstring. Readiness is established separately, immediately
        before the start, because liveness proven now says nothing about
        liveness at start time.

        ``accepting_user_id`` is carried into the CAS rather than checked
        before it: consent is the fact that authorises spending an owner's
        money, so the write itself must prove the writer owns the agent.
        """
        return await self.repo.accept_as_owner(battle_id, accepting_user_id)

    async def decline(self, battle_id: str, declining_user_id: str) -> dict | None:
        """Record B's owner refusal and start the cooldown. Does not commit.

        ``declining_user_id`` is carried into the CAS for the same reason accept
        carries it: a decline kills someone's battle and stamps a cooldown on
        the challenger, so the write must prove who asked for it.

        The cooldown is written in the same transaction as the decline, so
        "refused" and "may not immediately re-ask" become true together. Two
        statements across two transactions would leave a window in which the
        challenger can re-send against a target that has just said no.
        """
        battle = await self.repo.decline_as_owner(battle_id, declining_user_id)
        if battle is None:
            return None
        await self.repo.upsert_cooldown(
            challenger_agent_id=str(battle["agent_a_id"]),
            target_agent_id=str(battle["agent_b_id"]),
            cooldown_seconds=DECLINE_COOLDOWN_SECONDS,
        )
        return battle

    # -- reservation & readiness -------------------------------------------

    async def arm_readiness(self, battle_id: str) -> dict | None:
        """accepted -> reserved: reserve BOTH fighters and arm ready-checks.

        Does not commit — the caller owns the boundary, because every step here
        must land together or not at all: reservations that outlive a failed
        arming would strand two agents.

        Returns the armed battle row, or None if the battle was not in a state
        that permits arming. Raises ReservationConflictError when either
        fighter is already reserved elsewhere; there is no partial outcome to
        report, because reserve_both makes one physically uncommittable.

        The two ready-check events are inserted here, in this transaction, and
        dispatched by the caller AFTER commit. deliver_event() opens its own
        session and cannot join this transaction, so calling it here would
        create the failure window it exists to avoid.
        """
        battle = await self.repo.get(battle_id)
        if battle is None or battle["agent_b_id"] is None:
            return None

        agent_a_id = str(battle["agent_a_id"])
        agent_b_id = str(battle["agent_b_id"])

        # Raises on conflict — deliberately not caught here. The caller maps it
        # to 409; swallowing it would queue a battle with one fighter reserved.
        await self.repo.reserve_both(
            battle_id=battle_id,
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            reserved_until_seconds=RESERVATION_SECONDS,
        )

        event_id_a = await self._create_ready_check(battle_id, agent_a_id, "a")
        event_id_b = await self._create_ready_check(battle_id, agent_b_id, "b")

        armed = await self.repo.arm_readiness(
            battle_id=battle_id,
            ready_check_event_id_a=event_id_a,
            ready_check_event_id_b=event_id_b,
            ready_lease_seconds=READY_LEASE_SECONDS,
        )
        if armed is None:
            # Lost the CAS: the battle moved. Drop the reservations we just
            # took in this same transaction rather than leaving two agents
            # pinned to a battle that never armed.
            await self.repo.release_reservations(battle_id)
            return None
        return armed

    async def _create_ready_check(
        self, battle_id: str, agent_id: str, side: str
    ) -> str:
        """Persist ONE battle_ready_check outbox row. Returns its event_id.

        The TTL is the readiness lease, never the 32400s default: an event that
        stays ACK-able for nine hours is not a readiness check, it is a
        souvenir. The row is created here so its id can be armed onto the
        battle in the same transaction — readiness is bound to THIS id.
        """
        return await self.events.create(
            target_agent_id=agent_id,
            event_type="battle_ready_check",
            payload={
                "type": "battle_ready_check",
                "battle_id": str(battle_id),
                "side": side,
            },
            ttl_seconds=READY_LEASE_SECONDS,
        )

    async def dispatch_ready_checks(self, battle: dict) -> dict[str, str]:
        """Push the armed ready-checks. Call AFTER the arming transaction commits.

        The rows are already durable, so this is a convenience: a failure here
        costs latency, not the event — the heartbeat drain still carries it,
        and if that happens inside the lease it is a perfectly valid path to
        readiness.

        The returned DeliveryResults are for logging and the owner UI ONLY.
        DELIVERED here does not mean ready and must never be treated as such;
        QUEUED does not mean failed. Readiness is decided exclusively by
        try_queue(), against the ACKs.
        """
        results: dict[str, str] = {}
        for side, agent_key, event_key in (
            ("a", "agent_a_id", "ready_check_event_id_a"),
            ("b", "agent_b_id", "ready_check_event_id_b"),
        ):
            # dispatch_existing, not deliver_event: the rows were armed inside
            # the readiness transaction and readiness is bound to those exact
            # ids. deliver_event would insert a SECOND row for this durable
            # type, and a fighter acking the duplicate would never become ready.
            #
            # No ttl argument, because there is no second TTL to compute. The
            # row's expires_at was set from READY_LEASE_SECONDS by
            # _create_ready_check in the SAME transaction that set
            # ready_lease_expires_at from the same constant, and NOW() is the
            # transaction timestamp — so the event expires at the instant the
            # lease does, by construction rather than by arithmetic. The drift
            # only ever existed because the duplicate was stamped with a fresh
            # TTL at dispatch time, which is later than the arming.
            result = await dispatch_existing(
                str(battle[agent_key]), str(battle[event_key])
            )
            results[side] = result.value
            if result is DeliveryResult.FAILED:
                logger.warning(
                    "Ready-check dispatch failed for battle {} side {}",
                    battle["id"], side,
                )
        return results

    async def try_queue(self, battle_id: str, readiness_generation: int) -> dict | None:
        """reserved -> queued, iff every admission condition holds. No commit.

        Returns the queued battle, or None when it is not (yet) admissible.
        None is not an error: the usual reason is that an agent simply has not
        acked yet, and the caller retries until the lease lapses.

        One statement does all of it — consent, eligibility, ownership, live
        reservations and both exact ready-ACKs. Readiness alone was never the
        whole question: an agent can change owner or be deactivated after
        acking, and the reservations can be reaped out from under a battle that
        is still holding a live lease.
        """
        return await self.repo.admit_to_queue(battle_id, readiness_generation)

    async def release_expired_readiness(self, battle_id: str) -> dict | None:
        """reserved -> accepted once the lease lapsed. Frees BOTH. No commit.

        Both reservations are released in the same transaction as the state
        change, so a fighter is never left reserved for a battle that has
        stopped waiting for it. Rating is untouched: no shared start ever
        happened, so there is nothing to score.
        """
        released = await self.repo.release_readiness(battle_id)
        if released is None:
            return None
        await self.repo.release_reservations(battle_id)
        return released


def get_battle_service(db: AsyncSession) -> BattleService:
    """Build a BattleService over a request-scoped session."""
    return BattleService(db)


# ReservationConflictError is re-exported so the router can branch on it
# without importing the repository layer directly.
__all__ = [
    "BattleService",
    "ChallengeDeniedError",
    "LimiterUnavailableError",
    "ReservationConflictError",
    "get_battle_service",
]
