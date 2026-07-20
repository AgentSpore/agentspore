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

from datetime import UTC, date, datetime
from enum import Enum

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.redis_client import get_redis
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.battle_repo import (
    BattleRepository,
    ChallengeDenial,
    ReservationConflictError,
)
from app.schemas.battles import BattleStatus, TaskStatus
from app.services.battle_budget import (
    BattleJudgeBudgetService,
    breaker_is_open,
    breaker_record_attempt,
    breaker_record_failure,
    current_budget_day,
)
from app.services.battle_task_validator import (
    VALIDATION_MODEL,
    ValidationTransportError,
    run_cheap_filters,
    validate_with_llm,
)
from app.services.connection_manager import DeliveryResult, dispatch_existing
from app.services.openrouter_service import OpenRouterService

# How long a challenge waits for B's owner to answer. Consent is a human
# decision, so this is generous — hours, not seconds.
CHALLENGE_TTL_SECONDS = 86_400

# Inbound active-pending cap: at most N UNANSWERED challenges may be waiting on
# ONE target OWNER at a time (V68 E). Deliberately per-TARGET-OWNER, not
# per-agent or per-challenger: the budget a challenge spends belongs to the
# target's owner, so the limit that protects them must count everything landing
# on them across all their agents. It counts only status='challenge_pending'
# rows still inside challenge_expires_at — not a rolling time window — so a
# griefer cannot fill the cap with expired/declined history, and answering a
# challenge (accept/decline/expire) frees a slot immediately.
TARGET_CHALLENGE_CAP = 5

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

# How many readiness re-arm attempts (accepted -> reserved) a battle may burn
# before it is aborted rather than re-armed again. Each arm bumps
# readiness_generation, so the generation IS the attempt count. Without a bound,
# an opponent that accepts and then never ACKs re-reserves the challenger every
# READY_LEASE_SECONDS for the whole CHALLENGE_TTL_SECONDS (24h) — making the
# challenger unavailable for other battles with no Elo consequence. Three
# attempts is enough for an agent that briefly missed a ready-check to catch the
# next one, and short enough that a silent opponent cannot grief for a day.
READY_MAX_GENERATIONS = 3

# The abort reason recorded when both fighters proved readiness but no fresh
# task matched the requested category/difficulty at binding time (V67). Honest
# and specific: the battle did not fail for lack of an opponent or a missed
# ACK, it failed because the pool the challenger asked for was exhausted.
POOL_EXHAUSTED_REASON = (
    "task pool exhausted for requested category/difficulty"
)


def normalize_task_category(category: str | None) -> str | None:
    """Trim and lowercase a task category, or pass None through unchanged.

    The ONE normalisation both the challenge filter and task creation use, so
    "Backend", " backend " and "backend" bucket together. None ("any") stays
    None. A value that is blank after trimming also becomes None: an all-space
    category is not a category, and the Pydantic ``min_length=1`` on the request
    only rejects an empty string, not "   ".
    """
    if category is None:
        return None
    trimmed = category.strip().lower()
    return trimmed or None


# How many tasks one account may submit per day (V70). Deliberately small: each
# accepted submission spends an LLM call from the SAME budget the judge panel
# draws on, so an unbounded submitter competes with live battles for it. The
# LLM-call budget is a second, independent ceiling — this one bounds how much of
# the moderator queue a single account can fill, which no spend limit does.
DAILY_TASK_SUBMISSION_LIMIT = 5


class TaskSubmissionDenial(str, Enum):
    """Why a submission created nothing at all.

    Distinct from a REJECTION, which is a stored verdict about a task that does
    exist. A denial means there is no row: the caller gets a status code, not a
    submission id.
    """

    DAILY_QUOTA_EXHAUSTED = "daily_quota_exhausted"
    # Lost the race to a concurrent identical submission. Reported separately
    # from the cheap-filter duplicate rejection because the outcomes differ: this
    # one has nothing to show in "my submissions".
    DUPLICATE_CONTENT = "duplicate_content"


class TaskSubmissionDeniedError(Exception):
    """A submission was refused outright. ``reason`` names which gate."""

    def __init__(self, reason: TaskSubmissionDenial):
        self.reason = reason
        super().__init__(reason.value)


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

    def __init__(
        self,
        db: AsyncSession,
        session_factory: async_sessionmaker | None = None,
    ):
        self.db = db
        self.repo = BattleRepository(db)
        self.events = AgentEventRepository(db)
        # The budget ledger reserves in its OWN short transaction, committed
        # before the provider request, so it cannot share the request session.
        # Injectable because "which database" is exactly what a test needs to
        # redirect; defaults to the app factory so no caller has to know.
        self._session_factory = session_factory or async_session_maker

    # -- admission ----------------------------------------------------------

    async def _check_challenger_rate_limit(self, challenger_owner_user_id: str) -> None:
        """Fail-CLOSED per-OWNER rate limit. Raises, never returns False.

        Keyed on the verified challenger OWNER (V68 C1), not the agent: all of an
        owner's agents share one 20/hour limit, so a Sybil second agent cannot
        multiply an owner's fan-out across many targets.

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
            key = f"battle:challenge:owner-ratelimit:{challenger_owner_user_id}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, CHALLENGER_RATE_WINDOW_SECONDS)
        except Exception as exc:
            logger.warning(
                "Challenge limiter unavailable for owner {}: {}",
                challenger_owner_user_id, exc,
            )
            raise LimiterUnavailableError from exc
        if count > CHALLENGER_RATE_LIMIT:
            raise ChallengeDeniedError(ChallengeDenial.CHALLENGER_RATE_LIMITED)

    async def create_challenge(
        self,
        task_category: str | None,
        task_difficulty: str | None,
        agent_a_id: str,
        challenger_owner_user_id: str,
        agent_b_id: str | None = None,
        is_demo: bool = False,
    ) -> str:
        """Create a challenge over a task FILTER. Returns the battle id. No commit.

        The challenger names a category and difficulty (either may be None =
        "any"), never a task id (V67): the concrete task is chosen only at
        binding, after both sides prove readiness, so nothing can be
        precomputed. The category is normalised (trimmed + lowercased) so
        "Backend", " backend " and "backend" bucket together with the tasks,
        which are created through the same normalisation.

        ``agent_b_id=None`` opens the challenge to any eligible claimant.

        Order matters. The limiter runs BEFORE the insert, because a limiter
        that runs after has already let the row exist. The advisory lock runs
        before the insert too, so the per-target cap counts against a target
        nobody else is concurrently challenging.

        Raises ChallengeDeniedError (a rule refused, incl. an exhausted task
        pool) or LimiterUnavailableError (we could not tell). Both leave no
        battle row — the caller's transaction is rolled back on the exception
        path and, more fundamentally, the insert itself re-checks every rule and
        simply matches no rows.
        """
        await self._check_challenger_rate_limit(challenger_owner_user_id)

        category = normalize_task_category(task_category)

        target_owner: str | None = None
        if agent_b_id:
            # Resolve the target owner FIRST, then serialise on it (V68): the
            # active-pending cap is an owner-level cap, so the lock must be keyed
            # on the owner every concurrent challenger of this target shares. An
            # ownerless target is left unlocked — its eligibility predicate
            # refuses the challenge regardless.
            target_owner = await self._require_owner_snapshot(agent_b_id)
            if target_owner is not None:
                await self.repo.lock_challenge_target(target_owner)

        denial = await self.repo.diagnose_challenge(
            task_category=category,
            task_difficulty=task_difficulty,
            agent_a_id=agent_a_id,
            challenger_owner_user_id=challenger_owner_user_id,
            agent_b_id=agent_b_id,
            target_cap=TARGET_CHALLENGE_CAP,
            agent_b_owner_snapshot=target_owner,
        )
        if denial is not None:
            raise ChallengeDeniedError(denial)

        battle_id = await self.repo.create_challenge(
            task_category=category,
            task_difficulty=task_difficulty,
            agent_a_id=agent_a_id,
            agent_a_owner_snapshot=challenger_owner_user_id,
            challenge_ttl_seconds=CHALLENGE_TTL_SECONDS,
            target_cap=TARGET_CHALLENGE_CAP,
            agent_b_id=agent_b_id,
            agent_b_owner_snapshot=target_owner,
            is_demo=is_demo,
        )
        if battle_id is None:
            # The diagnostic said yes and the insert still refused: something
            # changed underneath us. Report the denial rather than a 500 — the
            # outcome (no row) is exactly what a denial means.
            logger.info(
                "Challenge insert refused after clean diagnosis: a={} b={}",
                agent_a_id, agent_b_id,
            )
            raise ChallengeDeniedError(ChallengeDenial.INSUFFICIENT_TASK_POOL)
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
        # Lock on the CLAIMANT'S OWNER (V68): an open challenge lands on the
        # claimant, so it counts against the claimant owner's active-pending cap
        # and must serialise against every other challenge arriving at that owner.
        await self.repo.lock_challenge_target(claiming_user_id)
        return await self.repo.claim_open_challenge_as_owner(
            battle_id=battle_id,
            agent_b_id=agent_b_id,
            claiming_user_id=claiming_user_id,
            target_cap=TARGET_CHALLENGE_CAP,
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

        Acceptance is also where the rated-eligibility decision is frozen (V68
        C2): both owner snapshots are now known, so under transaction-scoped
        advisory locks on both owners this evaluates the anti-Sybil gate
        (distinct + verified + old-enough + within the concurrent/daily rated
        quotas) and writes the verdict inside the same acceptance CAS. An
        ineligible battle still accepts — it simply runs unrated.
        """
        battle = await self.repo.get(battle_id)
        if battle is None or battle.get("agent_b_owner_snapshot") is None:
            # No opponent yet (open/unclaimed) or no such battle: nothing to
            # consent to, and no owner pair to decide rating for.
            return None

        owner_a = str(battle["agent_a_owner_snapshot"])
        owner_b = str(battle["agent_b_owner_snapshot"])

        # Lock both owners before reading their rated counts, so two concurrent
        # accepts near a quota cannot both pass.
        await self.repo.lock_rating_owners([owner_a, owner_b])
        rated_eligible, quota_day, reason = await self._decide_rated_eligibility(
            owner_a, owner_b, is_demo=bool(battle.get("is_demo"))
        )
        return await self.repo.accept_as_owner(
            battle_id,
            accepting_user_id,
            rated_eligible=rated_eligible,
            rated_quota_day=quota_day,
            rated_ineligibility_reason=reason,
        )

    # The rated-ineligibility reason vocabulary lives here, next to the gate that
    # produces the rest of it ("same_owner", "account_unverified",
    # "account_too_new", "owner_concurrent_quota", "owner_daily_quota"), so the
    # set a caller must understand is readable in one place.
    #
    # This one is the odd member: it is the ONLY reason _decide_rated_eligibility
    # cannot itself return. The rated verdict is frozen at acceptance
    # (challenge_pending -> accepted), while the task is bound much later, at
    # reserved -> queued — so at the moment this gate runs there is no task to
    # inspect. It is produced by battle_runner.settle_battle instead, from the
    # bound task, and recorded through finalize(rated_ineligibility_reason=...).
    #
    # It should never actually be recorded: admit_to_queue's pool split (V70)
    # makes a rated battle structurally unable to bind a quarantined task. If
    # this string ever appears in the data, the pool split has a hole and the
    # settle-time backstop is what stopped a user-authored task from moving Elo.
    TASK_IN_QUARANTINE_REASON = "task_in_quarantine"

    async def _decide_rated_eligibility(
        self, owner_a: str, owner_b: str, is_demo: bool = False
    ) -> tuple[bool, date | None, str | None]:
        """The phase-1 anti-Sybil rated gate. Returns (eligible, quota_day, reason).

        Rules are evaluated most-specific-first so the recorded reason names the
        first one that bit. Every FALSE outcome still yields a perfectly valid,
        judged-for-fun battle; it just cannot move Elo. Call under
        :meth:`BattleRepository.lock_rating_owners` for the counts to be race-free.

        The demo rule is FIRST and is the WHOLE rating suppression for demo mode:
        a battle against the platform demo opponent is a showcase, not a contest,
        so it can never move Elo even between two distinct, verified, aged owners
        that would otherwise rate. Nothing else about the rated path changes.
        """
        settings = get_settings()

        if is_demo:
            return (False, None, "demo")

        if owner_a == owner_b:
            return (False, None, "same_owner")

        verified, aged = await self.repo.owner_accounts_ok(
            [owner_a, owner_b], settings.battle_rated_min_account_age_days
        )
        if not verified:
            return (False, None, "account_unverified")
        if not aged:
            return (False, None, "account_too_new")

        for owner in (owner_a, owner_b):
            if (
                await self.repo.count_owner_active_rated(owner)
                >= settings.battle_owner_concurrent_rated_limit
            ):
                return (False, None, "owner_concurrent_quota")

        today = current_budget_day()
        for owner in (owner_a, owner_b):
            if (
                await self.repo.count_owner_rated_for_day(owner, today)
                >= settings.battle_owner_daily_rated_limit
            ):
                return (False, None, "owner_daily_quota")

        return (True, today, None)

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
        # A decline is reached from challenge_pending, which normally holds no
        # reservations — but release them in the same transaction anyway, so no
        # terminal path can leave a fighter pinned to a battle that has ended.
        await self.repo.release_reservations(battle_id)
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

    async def try_queue(
        self, battle_id: str, readiness_generation: int, lease_token: str
    ) -> dict | None:
        """reserved -> queued AND bind a task, iff every condition holds. No commit.

        Returns the queued+bound battle, or None when it is not (yet)
        admissible — including the pool-exhausted case, which the caller
        distinguishes via :meth:`abort_pool_exhausted`. None is not an error:
        the usual reason is that an agent simply has not acked yet, and the
        caller retries until the lease lapses.

        One statement does all of it — consent, eligibility, ownership, live
        reservations, both exact ready-ACKs, the processing lease, AND the task
        binding (random fresh task matching the filter, snapshotted). Readiness
        alone was never the whole question: an agent can change owner or be
        deactivated after acking, and the reservations can be reaped out from
        under a battle that is still holding a live lease. The ``lease_token`` is
        the reconciler claim token, so only the worker that claimed the reserved
        row may bind it (V67).
        """
        return await self.repo.admit_to_queue(
            battle_id, readiness_generation, lease_token
        )

    async def abort_pool_exhausted(
        self, battle_id: str, readiness_generation: int, lease_token: str
    ) -> dict | None:
        """reserved -> aborted when readiness holds but the task pool is empty.

        The terminal for a rated challenge whose filter has fewer than the
        minimum fresh tasks at binding time (V67). Distinct from a readiness
        lapse: here BOTH sides ACKed, so the abort CAS re-proves the full
        readiness/lease/eligibility/ACK predicate set and asserts the pool
        really is below the minimum before firing — a battle merely still
        waiting for an ACK falls through as None and is retried, never aborted.

        Releases both reservations in the same transaction; Elo is untouched (no
        shared start happened). Returns the aborted row, or None when the battle
        was not in the exhausted shape. Does not commit — the caller owns the
        boundary and fires the terminal owner notification after committing.
        """
        aborted = await self.repo.abort_pool_exhausted(
            battle_id=battle_id,
            readiness_generation=readiness_generation,
            lease_token=lease_token,
            verdict_reason=POOL_EXHAUSTED_REASON,
        )
        if aborted is None:
            return None
        await self.repo.release_reservations(battle_id)
        return aborted

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

    async def expire_or_abort_readiness(
        self, battle_id: str, max_generations: int = READY_MAX_GENERATIONS
    ) -> dict | None:
        """On a lapsed ready lease: re-arm-able -> accepted, else -> aborted.

        The grief-bounded successor to release_expired_readiness for the
        reconciler's reserved-poll path. When the ready lease has lapsed:

        * if the battle has NOT spent its re-arm budget
          (readiness_generation < max_generations), release it back to
          'accepted' exactly as before, freeing both fighters for the next arm;
        * once the budget is spent, ABORT it instead of re-arming, naming the
          silent side(s), and release both reservations — otherwise a never-ACK
          opponent keeps the challenger reserved for the whole challenge TTL.

        Returns a small outcome dict (``{"outcome": "released"|"aborted",
        "battle": <row>, "silent_sides": (...)}``) or None when the battle is
        not in a state to act on (not reserved, or the lease is still live). Does
        not commit — the caller owns the boundary and, on an abort, also fires
        the terminal owner notification after committing.
        """
        battle = await self.repo.get(battle_id)
        if battle is None or battle["status"] != BattleStatus.RESERVED.value:
            return None
        lease = battle["ready_lease_expires_at"]
        if lease is None or lease > datetime.now(UTC):
            # Readiness is still legitimately in flight — nothing to release yet.
            return None

        if battle["readiness_generation"] >= max_generations:
            silent = await self.repo.unacked_ready_sides(battle_id)
            aborted = await self.repo.abort_unready_readiness(
                battle_id, max_generations, _unready_abort_reason(silent, max_generations)
            )
            if aborted is None:
                return None
            await self.repo.release_reservations(battle_id)
            return {"outcome": "aborted", "battle": aborted, "silent_sides": silent}

        released = await self.repo.release_readiness(battle_id)
        if released is None:
            return None
        await self.repo.release_reservations(battle_id)
        return {"outcome": "released", "battle": released, "silent_sides": ()}

    # -- demo auto-drive ----------------------------------------------------

    async def create_demo_battle(
        self,
        *,
        agent_a_id: str,
        challenger_owner_user_id: str,
        demo_agent_id: str,
        task_category: str | None,
        task_difficulty: str | None,
    ) -> str:
        """Create a demo challenge AND accept it inline, in ONE transaction. No commit.

        A demo battle needs no human consent on the opponent side, so acceptance
        is folded into creation: the row is written 'challenge_pending' and driven
        straight to 'accepted' before the caller commits, so it NEVER exists as a
        visible 'challenge_pending' row. That removes the ~30s a demo user would
        otherwise wait for the reconciler's auto-accept tick AND keeps the battle
        off TARGET_CHALLENGE_CAP entirely — the cap counts 'challenge_pending'
        rows, and this one is only ever committed already 'accepted', so a burst of
        new demo users cannot starve each other on it.

        The reconciler's auto-accept stays the crash backstop: a crash before this
        accept never commits the challenge at all, and a crash after commit leaves
        it already 'accepted' — neither ships a half-open state. On the (in one
        uncommitted transaction, unreachable) chance the inline accept matches no
        row, the battle stays 'challenge_pending' and that backstop consents on the
        next tick, so this never raises for a stale-accept.

        The rated verdict is frozen exactly as for any accept: being demo, it is
        (False, 'demo'), so the battle is unrated before it ever arms. Raises the
        same ChallengeDeniedError / LimiterUnavailableError create_challenge does.
        """
        battle_id = await self.create_challenge(
            task_category=task_category,
            task_difficulty=task_difficulty,
            agent_a_id=agent_a_id,
            challenger_owner_user_id=challenger_owner_user_id,
            agent_b_id=demo_agent_id,
            is_demo=True,
        )
        await self.auto_accept_demo(battle_id)
        return battle_id

    async def auto_accept_demo(self, battle_id: str) -> dict | None:
        """challenge_pending -> accepted for a demo battle, with NO human action.

        The platform demo opponent (always agent_b, owned by the seeding admin)
        has no owner sitting at a UI to click accept, so the reconciler consents
        on its behalf — as the demo agent's OWN owner, which is exactly what
        accept_as_owner's CAS requires (accepting user == agent_b_owner_snapshot
        == current owner). Consent runs through the ordinary :meth:`accept`, so
        the frozen rated verdict is written the same way — and, being demo, it is
        (False, 'demo'): the battle is unrated before it ever arms.

        Does not commit — the caller owns the boundary. Returns None when the
        battle is not a consent-able demo challenge (already accepted, expired,
        not a demo, or no opponent), which the caller treats as a no-op.
        """
        battle = await self.repo.get(battle_id)
        if (
            battle is None
            or not battle.get("is_demo")
            or battle["agent_b_id"] is None
            or battle.get("agent_b_owner_snapshot") is None
        ):
            return None
        return await self.accept(battle_id, str(battle["agent_b_owner_snapshot"]))

    async def synth_demo_ready_ack(self, battle: dict) -> list[str]:
        """ACK the demo opponent's CURRENT ready-check on its behalf. Idempotent.

        A real opponent proves readiness by ACKing the ready-check its heartbeat
        drained; the demo opponent has no live agent, so the platform records the
        same fact for it. Keyed to the exact armed event id on the battle row
        (``ready_check_event_id_b``, side B — the demo opponent is always agent_b),
        so a re-armed generation acks the NEW event, never a stale one.

        :meth:`AgentEventRepository.mark_acked` is a no-op when the event is
        already acked or has expired, so re-running a reconcile tick — or a
        heartbeat that happened to beat us to it — cannot move acked_at or ack an
        expired event. Does not commit; the ack is visible to the caller's own
        subsequent statements (same transaction, READ COMMITTED) and is what
        admit_to_queue's both-sides-acked predicate then reads.
        """
        event_id = battle.get("ready_check_event_id_b")
        if event_id is None or battle.get("agent_b_id") is None:
            return []
        return await self.events.mark_acked(
            str(battle["agent_b_id"]), [str(event_id)]
        )

    # -- user task submission (V70) -----------------------------------------

    async def submit_task(
        self,
        *,
        user_id: str,
        title: str,
        prompt: str,
        rubric: list[dict],
        category: str,
        difficulty: str,
        time_limit_seconds: int,
    ) -> dict:
        """Accept a task submission, then validate it. Owns its transactions.

        The order is the whole design:

        1. the daily quota, because it costs one indexed count and refusing here
           spends nothing else;
        2. the cheap filters (shape, dedup, injection), which decide without a
           provider — a submission refused here NEVER reaches the LLM, so a
           duplicate cannot spend a judging call to be told it is a duplicate;
        3. the row, COMMITTED before any provider request, so a crash or a budget
           refusal leaves a submission the author can see rather than losing it;
        4. one LLM call, whose verdict lands in a second transaction.

        A cheap-filter refusal is still stored, as a 'rejected' row: the author
        asked a question and deserves the answer in ``GET /battles/tasks/mine``
        rather than only in an HTTP response they may never see again.

        Returns ``{"id", "status", "reason"}``. Raises
        :class:`TaskSubmissionDeniedError` when nothing was created at all.
        """
        # Serialise this submitter against themselves BEFORE counting. The count
        # is a read under READ COMMITTED, so without the lock N concurrent
        # submissions all see the same total, all find room and all insert — the
        # quota would leak by the number of concurrent callers. Held until the
        # insert below commits, which is what makes the count that authorised it
        # still true when the row lands.
        await self.repo.lock_submitter(user_id)
        used_today = await self.repo.count_submissions_today(user_id)
        if used_today >= DAILY_TASK_SUBMISSION_LIMIT:
            # Roll back so the advisory lock is released now rather than
            # whenever this request's session happens to close: nothing was
            # written, and a denied submitter must not block their own retry.
            await self.db.rollback()
            raise TaskSubmissionDeniedError(TaskSubmissionDenial.DAILY_QUOTA_EXHAUSTED)

        duplicate = await self.repo.content_key_exists(prompt)
        cheap = run_cheap_filters(
            title=title, prompt=prompt, rubric=rubric, duplicate_exists=duplicate
        )
        status = TaskStatus.PENDING_VALIDATION if cheap.passed else TaskStatus.REJECTED
        verdict_document = (
            None
            if cheap.passed
            else {"stage": "cheap_filters", "reason": cheap.reason, "detail": cheap.detail}
        )

        try:
            task_id = await self.repo.create_submission(
                user_id=user_id,
                title=title,
                prompt=prompt,
                rubric=rubric,
                time_limit_seconds=time_limit_seconds,
                category=category,
                difficulty=difficulty,
                status=status,
                validation_reason=cheap.reason,
                validation_verdict=verdict_document,
            )
            await self.db.commit()
        except IntegrityError:
            # The dedup index fired: a concurrent submission of the same
            # canonical content committed between our read and our insert. The
            # read above cannot close that window; the index can, which is why it
            # exists. Nothing was created, so this is a refusal, not a rejection.
            await self.db.rollback()
            raise TaskSubmissionDeniedError(
                TaskSubmissionDenial.DUPLICATE_CONTENT
            ) from None

        if not cheap.passed:
            return {"id": task_id, "status": status.value, "reason": cheap.reason}

        return await self.validate_submission(
            task_id=task_id,
            user_id=user_id,
            title=title,
            prompt=prompt,
            rubric=rubric,
            category=category,
            difficulty=difficulty,
            time_limit_seconds=time_limit_seconds,
        )

    async def validate_submission(
        self,
        *,
        task_id: str,
        user_id: str,
        title: str,
        prompt: str,
        rubric: list[dict],
        category: str,
        difficulty: str,
        time_limit_seconds: int,
    ) -> dict:
        """Spend ONE budgeted LLM call on a pending submission and record it.

        Every refusal short of a verdict leaves the task in 'pending_validation'
        and returns normally. That is the deliberate shape: no provider
        configured, an exhausted judge budget and a transport failure are all
        facts about the PLATFORM, and none of them is evidence about the task.
        Rejecting on any of them would punish a submitter for our outage, and
        raising would answer a submission that WAS accepted with a 500 — so a
        pending task simply waits for a later pass.

        The budget is the judge panel's, on purpose (see
        ``reserve_validation_call``): validation that could spend past an
        exhausted judge cap would mean the cap is not a cap. For the same
        reason the CIRCUIT BREAKER applies here too — checked before reserving,
        fed by this call's own attempt and failure. Sharing the budget while
        ignoring the breaker would mean validation kept spending into a provider
        outage that judging had already backed off from, and that its own
        failures never counted toward opening it.
        """
        if await breaker_is_open():
            # Treated exactly like an exhausted budget: refuse softly, leave the
            # task pending. The breaker is a transient incident signal, so a
            # later pass validates the same submission once it closes.
            logger.warning("task {} left pending: judge breaker open", task_id)
            return {
                "id": task_id,
                "status": TaskStatus.PENDING_VALIDATION.value,
                "reason": None,
            }

        provider = OpenRouterService().resolve_provider(VALIDATION_MODEL)
        if provider is None:
            logger.warning(
                "task {} left pending: no usable provider for {}",
                task_id,
                VALIDATION_MODEL,
            )
            return {
                "id": task_id,
                "status": TaskStatus.PENDING_VALIDATION.value,
                "reason": None,
            }

        budget = BattleJudgeBudgetService(self._session_factory)
        reservation = await budget.reserve_validation_call(
            user_id=user_id,
            provider=VALIDATION_MODEL.split("/")[0],
            model=VALIDATION_MODEL,
        )
        if not reservation.granted:
            logger.warning(
                "task {} left pending: validation budget refused ({})",
                task_id,
                reservation.reason,
            )
            return {
                "id": task_id,
                "status": TaskStatus.PENDING_VALIDATION.value,
                "reason": None,
            }

        # Counted before transmitting, like the judge path: the breaker's spike
        # rule measures attempts made, and an attempt recorded only on success
        # would never see the storm it exists to detect.
        await breaker_record_attempt()
        try:
            verdict = await validate_with_llm(
                base_url=provider["base_url"],
                api_key=provider["api_key"],
                title=title,
                prompt=prompt,
                rubric=rubric,
                category=category,
                difficulty=difficulty,
                time_limit_seconds=time_limit_seconds,
            )
        except ValidationTransportError as exc:
            await budget.settle_call(
                str(reservation.ledger_id),
                succeeded=False,
                error_class=type(exc).__name__,
            )
            # A permanent failure (zero balance, rejected key) opens the breaker
            # at once — no backoff creates money — while transient ones only
            # count toward the threshold.
            await breaker_record_failure(permanent=exc.permanent)
            logger.warning("task {} validation call failed: {}", task_id, exc)
            return {
                "id": task_id,
                "status": TaskStatus.PENDING_VALIDATION.value,
                "reason": None,
            }

        await budget.settle_call(str(reservation.ledger_id), succeeded=True)

        status = TaskStatus.QUARANTINE if verdict.accepted else TaskStatus.REJECTED
        reason = None if verdict.accepted else "; ".join(verdict.reasons)[:500]
        applied = await self.repo.record_validation_outcome(
            task_id, status=status, reason=reason, verdict=verdict.as_document()
        )
        await self.db.commit()
        if not applied:
            # Someone else decided this submission first — a moderator rejecting
            # it while this call sat on the provider for up to a minute. Their
            # decision stands, and the row is consistent; only the answer is in
            # question. Re-read the ACTUAL state and report that: the submitter
            # asked what happened to their task, and the truthful answer is what
            # the row says now, not what this pass computed and could not write.
            # Returning a null status instead would fail SubmitTaskResponse
            # validation and 500 a request the server handled correctly.
            logger.info("task {} already left pending_validation; verdict dropped", task_id)
            current = await self.repo.get_submission_state(task_id)
            if current is None:
                return {
                    "id": task_id,
                    "status": TaskStatus.REJECTED.value,
                    "reason": "submission is no longer available",
                }
            return {
                "id": task_id,
                "status": current["status"],
                "reason": current["validation_reason"],
            }
        return {"id": task_id, "status": status.value, "reason": reason}

    async def approve_task(self, task_id: str, approver_user_id: str) -> bool:
        """Moderator promotion, quarantine -> ready. Commits on success.

        False means the task was not in quarantine — already rejected, already
        approved, or still awaiting validation. The repository decides that
        inside the UPDATE, so this never reads-then-writes.
        """
        approved = await self.repo.approve_submission(task_id, approver_user_id)
        if approved:
            await self.db.commit()
        return approved

    async def reject_task(self, task_id: str, reason: str) -> bool:
        """Moderator rejection of a pending or quarantined submission. Commits."""
        rejected = await self.repo.reject_submission(task_id, reason)
        if rejected:
            await self.db.commit()
        return rejected


def _unready_abort_reason(silent_sides: tuple[str, ...], max_generations: int) -> str:
    """Name the side(s) that never confirmed readiness, for the abort record."""
    if len(silent_sides) >= 2:
        who = "both fighters"
    elif silent_sides:
        who = f"fighter {silent_sides[0]}"
    else:
        # Defensive: an abort with nobody silent should not happen, but the
        # record must still be truthful rather than name an innocent side.
        who = "readiness"
    return (
        f"readiness never confirmed after {max_generations} attempts: "
        f"{who} did not ACK the ready-check"
    )


def get_battle_service(db: AsyncSession) -> BattleService:
    """Build a BattleService over a request-scoped session."""
    return BattleService(db)


# ReservationConflictError is re-exported so the router can branch on it
# without importing the repository layer directly.
__all__ = [
    "DAILY_TASK_SUBMISSION_LIMIT",
    "BattleService",
    "ChallengeDeniedError",
    "LimiterUnavailableError",
    "ReservationConflictError",
    "TaskSubmissionDenial",
    "TaskSubmissionDeniedError",
    "get_battle_service",
]
