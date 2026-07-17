"""BattleRepository — data access and the atomic battle state machine (V66).

Every transition here is a compare-and-set: one SQL statement that names the
expected old state in its WHERE clause and RETURNs the changed row. There is
no read-then-write anywhere in this module, because a read-then-write is a
race by construction — two workers both read 'accepted', both decide they may
proceed, and both proceed.

Reading the result is not optional. Zero rows returned means you LOST: another
caller moved the battle first, the battle is terminal, or the state was never
what you assumed. It is never "probably fine". Callers must branch on it.

The Redis leader lease is NOT the correctness boundary and must never be
treated as one: losing leadership only logs, it does not stop an in-flight
run_once(), so a former leader and a new leader can both reach these
statements. What keeps them honest is the CAS above and the per-row lease
tokens below — the database is the arbiter.

A lease token is not decoration. Every terminal write demands BOTH a matching
token AND a lease that has not lapsed: a token alone lets a worker whose lease
expired hours ago still publish its stale answer, which is the precise failure
the row lease exists to prevent.

Layering: this module imports nothing from app.services. Event delivery,
readiness decisions and judging all live a layer up (battle_service), which is
why no method here touches connection_manager or agent_event_repo.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.battles import BattleStatus, JudgeRunStatus, Side, TaskSource, TaskStatus


class ReservationConflictError(Exception):
    """Raised when both fighters could not be reserved in one statement.

    Deliberately an exception rather than a partial list. "Reserve both or
    neither" cannot be a docstring asking the caller to roll back — a caller
    that ignores the advice commits one row and strands a fighter reserved for
    a battle that never got an opponent. Raising inside the savepoint makes the
    partial insert physically uncommittable, so the invariant belongs to the
    system rather than to the diligence of a caller that does not exist yet.
    """


class ChallengeDenial(str, Enum):
    """Why an admission gate refused a challenge.

    Exists so the API can answer 403 vs 409 vs 429 truthfully. The gate that
    actually protects the target is the predicate set inside create_challenge's
    INSERT; this enum only names what a diagnostic read saw, so the caller can
    say WHICH rule bit rather than a generic "denied".
    """

    TASK_UNAVAILABLE = "task_unavailable"
    CHALLENGER_INELIGIBLE = "challenger_ineligible"
    # The CALLER exhausted its own hourly quota. Distinct from TARGET_CAPPED:
    # reporting "the target is full" to a challenger that is itself the problem
    # is a lie about someone else's state, and sends the owner to look at a
    # target that may be nowhere near its limit.
    CHALLENGER_RATE_LIMITED = "challenger_rate_limited"
    TARGET_INELIGIBLE = "target_ineligible"
    BLOCKED = "blocked"
    COOLING_DOWN = "cooling_down"
    TARGET_CAPPED = "target_capped"
    PAIR_ALREADY_ENGAGED = "pair_already_engaged"


# Namespace for pg_advisory_xact_lock, so battle challenge locks cannot collide
# with any other advisory-lock user in this database. Arbitrary but fixed.
CHALLENGE_LOCK_NAMESPACE = 0x62_74_6C_31  # "btl1"

# An agent is eligible to fight only while all four hold. Hosted agents are
# excluded because their inference is paid by the platform, not by an owner who
# opted in. Rendered once and interpolated into the statements below so the rule
# is defined in exactly one place — the values are literals, never user input.
_AGENT_ELIGIBLE_SQL = """
    a.is_active = TRUE
    AND a.is_hosted = FALSE
    AND a.available_for_battles = TRUE
    AND a.owner_user_id IS NOT NULL
"""

# A battle that still owes its fighters something. A pair may have exactly one
# of these at a time, and the challenge cap counts them.
_ENGAGED_STATUSES_SQL = (
    "('challenge_pending', 'accepted', 'reserved', 'queued', 'running', 'judging')"
)

# Both fighters still pass eligibility AND their current owner still equals the
# snapshot frozen into the battle. Re-checked at every consequential transition,
# never inherited from the challenge: owner_user_id is mutable
# (ownership.py:186 links an agent to a new user) and is_active goes FALSE as a
# SIDE EFFECT of revoke_github_oauth (agent_repo.py:140) — an agent drops out of
# eligibility silently, with nothing pointing at battles.
#
# Owner-equals-snapshot is the load-bearing half: the snapshots decide rating and
# reward, so a battle whose fighter changed hands mid-flight is a battle between
# parties who never both agreed. It must not start.
_BOTH_FIGHTERS_ELIGIBLE_SQL = f"""
    EXISTS (
        SELECT 1 FROM agents a
        WHERE a.id = battles.agent_a_id
          AND a.owner_user_id = battles.agent_a_owner_snapshot
          AND {_AGENT_ELIGIBLE_SQL}
    )
    AND EXISTS (
        SELECT 1 FROM agents a
        WHERE a.id = battles.agent_b_id
          AND a.owner_user_id = battles.agent_b_owner_snapshot
          AND {_AGENT_ELIGIBLE_SQL}
    )
"""

# Both fighters are STILL held by THIS battle's reservations, unexpired.
#
# Without this a battle can run holding nothing. delete_expired_reservations()
# reaps on wall-clock time alone and frees both rows the moment reserved_until
# passes; nothing re-checks afterwards. A battle queued at t=59 (lease alive) has
# its reservations reaped at t=90 and, unguarded, still starts at t=200 with zero
# rows held — both fighters simultaneously free to be reserved by another battle.
# That is the exact double-spend of the owners' keys reservations exist to stop.
_BOTH_FIGHTERS_RESERVED_SQL = """
    EXISTS (
        SELECT 1 FROM battle_reservations r
        WHERE r.agent_id = battles.agent_a_id
          AND r.battle_id = battles.id
          AND r.reserved_until > NOW()
    )
    AND EXISTS (
        SELECT 1 FROM battle_reservations r
        WHERE r.agent_id = battles.agent_b_id
          AND r.battle_id = battles.id
          AND r.reserved_until > NOW()
    )
"""

# Exactly the two armed ready-check events, acked by the agent each was armed
# for, inside both the event's own expiry and the readiness lease.
#
# The ids come from the battle row, never from a caller. Requiring COUNT = 2 over
# an IN of the two armed ids means one event acked twice cannot stand in for two.
_BOTH_SIDES_ACKED_SQL = """
    (
        SELECT COUNT(*) FROM agent_events e
        WHERE e.event_id IN (battles.ready_check_event_id_a,
                             battles.ready_check_event_id_b)
          AND e.type = 'battle_ready_check'
          AND e.acked_at IS NOT NULL
          AND e.acked_at < e.expires_at
          AND e.acked_at < battles.ready_lease_expires_at
          AND e.target_agent_id = CASE
                  WHEN e.event_id = battles.ready_check_event_id_a
                  THEN battles.agent_a_id ELSE battles.agent_b_id
              END
    ) = 2
"""


class BattleRepository:
    """All database operations for battles and their satellites."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # -- admission ----------------------------------------------------------

    async def lock_challenge_target(self, target_agent_id: str) -> None:
        """Serialise challenge creation against ONE target, until commit.

        Without this the per-target cap is not a cap: two concurrent
        challengers both COUNT the committed rows under READ COMMITTED, both
        see cap-1, and both insert — the boundary leaks by exactly the number
        of concurrent callers, which is the case a cap exists to stop.

        An advisory lock rather than ``SELECT ... FROM agents FOR UPDATE``:
        row-locking the agent would also block every heartbeat
        (``UPDATE agents SET last_heartbeat``) for as long as a challenge
        transaction runs. The contention we want is challenge-vs-challenge on
        one target, and nothing else. The lock is released by commit or
        rollback, so a crashed challenger cannot wedge a target.

        Open challenges (no target yet) skip this: there is no target to cap.
        """
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(:ns, hashtext(:target))"),
            {"ns": CHALLENGE_LOCK_NAMESPACE, "target": str(target_agent_id)},
        )

    async def diagnose_challenge(
        self,
        task_id: str,
        agent_a_id: str,
        challenger_owner_user_id: str,
        agent_b_id: str | None,
        target_cap: int,
        target_window_seconds: int,
    ) -> ChallengeDenial | None:
        """Name the first rule that refuses this challenge. None = admissible.

        Diagnostic ONLY. It is deliberately NOT the gate: a read that decides
        and an insert that acts are two statements, and between them the world
        moves. The real gate is the predicate set inside create_challenge,
        which re-checks every one of these in the INSERT itself. This exists so a
        refusal can say "cooldown" instead of "no", which a single boolean from
        the INSERT can never do.
        """
        result = await self.db.execute(
            text(
                f"""
                SELECT
                    EXISTS (
                        SELECT 1 FROM battle_tasks t
                        WHERE t.id = CAST(:task_id AS UUID) AND t.status = 'ready'
                    ) AS task_ok,
                    EXISTS (
                        SELECT 1 FROM agents a
                        WHERE a.id = CAST(:agent_a_id AS UUID)
                          AND a.owner_user_id = CAST(:challenger_owner AS UUID)
                          AND {_AGENT_ELIGIBLE_SQL}
                    ) AS challenger_ok,
                    (
                        CAST(:agent_b_id AS UUID) IS NULL
                        OR EXISTS (
                            SELECT 1 FROM agents a
                            WHERE a.id = CAST(:agent_b_id AS UUID)
                              AND {_AGENT_ELIGIBLE_SQL}
                        )
                    ) AS target_ok,
                    EXISTS (
                        SELECT 1 FROM battle_blocks bl
                        WHERE (bl.blocker_agent_id = CAST(:agent_b_id AS UUID)
                               AND bl.blocked_agent_id = CAST(:agent_a_id AS UUID))
                           OR (bl.blocker_agent_id = CAST(:agent_a_id AS UUID)
                               AND bl.blocked_agent_id = CAST(:agent_b_id AS UUID))
                    ) AS blocked,
                    EXISTS (
                        SELECT 1 FROM battle_challenge_cooldowns c
                        WHERE c.challenger_agent_id = CAST(:agent_a_id AS UUID)
                          AND c.target_agent_id = CAST(:agent_b_id AS UUID)
                          AND c.cooldown_until > NOW()
                    ) AS cooling,
                    (
                        CAST(:agent_b_id AS UUID) IS NOT NULL
                        AND (
                            SELECT COUNT(*) FROM battles b
                            WHERE b.agent_b_id = CAST(:agent_b_id AS UUID)
                              AND b.challenged_at
                                  > NOW() - make_interval(secs => :target_window)
                        ) >= :target_cap
                    ) AS capped,
                    EXISTS (
                        SELECT 1 FROM battles b
                        WHERE b.status IN {_ENGAGED_STATUSES_SQL}
                          AND (
                              (b.agent_a_id = CAST(:agent_a_id AS UUID)
                               AND b.agent_b_id = CAST(:agent_b_id AS UUID))
                              OR (b.agent_a_id = CAST(:agent_b_id AS UUID)
                                  AND b.agent_b_id = CAST(:agent_a_id AS UUID))
                          )
                    ) AS pair_engaged
                """
            ),
            {
                "task_id": str(task_id),
                "agent_a_id": str(agent_a_id),
                "challenger_owner": str(challenger_owner_user_id),
                "agent_b_id": str(agent_b_id) if agent_b_id else None,
                "target_cap": target_cap,
                "target_window": target_window_seconds,
            },
        )
        row = result.mappings().one()
        # Ordered most-specific-last so the message names the interesting rule:
        # a blocked pair is more informative than "target ineligible".
        if not row["task_ok"]:
            return ChallengeDenial.TASK_UNAVAILABLE
        if not row["challenger_ok"]:
            return ChallengeDenial.CHALLENGER_INELIGIBLE
        if not row["target_ok"]:
            return ChallengeDenial.TARGET_INELIGIBLE
        if row["blocked"]:
            return ChallengeDenial.BLOCKED
        if row["cooling"]:
            return ChallengeDenial.COOLING_DOWN
        if row["pair_engaged"]:
            return ChallengeDenial.PAIR_ALREADY_ENGAGED
        if row["capped"]:
            return ChallengeDenial.TARGET_CAPPED
        return None

    async def upsert_cooldown(
        self,
        challenger_agent_id: str,
        target_agent_id: str,
        cooldown_seconds: int,
    ) -> None:
        """Start (or extend) the decline cooldown for one ordered pair.

        GREATEST on conflict so a later decline can only push the cooldown
        further out. Taking the new value unconditionally would let a
        challenger shorten its own penalty by provoking a second, faster
        decline.
        """
        await self.db.execute(
            text(
                """
                INSERT INTO battle_challenge_cooldowns
                    (challenger_agent_id, target_agent_id, cooldown_until)
                VALUES
                    (CAST(:challenger AS UUID), CAST(:target AS UUID),
                     NOW() + make_interval(secs => :cooldown))
                ON CONFLICT (challenger_agent_id, target_agent_id) DO UPDATE
                SET cooldown_until = GREATEST(
                    battle_challenge_cooldowns.cooldown_until,
                    EXCLUDED.cooldown_until
                )
                """
            ),
            {
                "challenger": str(challenger_agent_id),
                "target": str(target_agent_id),
                "cooldown": cooldown_seconds,
            },
        )

    # -- tasks --------------------------------------------------------------

    async def create_task(
        self,
        source: TaskSource,
        title: str,
        prompt: str,
        rubric: list[dict[str, Any]],
        time_limit_seconds: int,
        category: str | None = None,
        created_by_user_id: str | None = None,
        status: TaskStatus = TaskStatus.READY,
    ) -> str:
        """Insert a battle task and return its id. Does not commit."""
        result = await self.db.execute(
            text(
                """
                INSERT INTO battle_tasks
                    (source, title, prompt, rubric, category,
                     time_limit_seconds, status, created_by_user_id)
                VALUES
                    (:source, :title, :prompt, CAST(:rubric AS JSONB), :category,
                     :time_limit_seconds, :status,
                     CAST(:created_by_user_id AS UUID))
                RETURNING id
                """
            ),
            {
                "source": source.value,
                "title": title,
                "prompt": prompt,
                "rubric": json.dumps(rubric, default=str),
                "category": category,
                "time_limit_seconds": time_limit_seconds,
                "status": status.value,
                "created_by_user_id": (str(created_by_user_id) if created_by_user_id else None),
            },
        )
        return str(result.scalar_one())

    # -- battles ------------------------------------------------------------

    async def _create_battle(
        self,
        task_id: str,
        agent_a_id: str,
        agent_a_owner_snapshot: str,
        challenge_ttl_seconds: int,
        agent_b_id: str | None = None,
        agent_b_owner_snapshot: str | None = None,
    ) -> str | None:
        """Insert a challenge row and return its id. Does not commit.

        The STATE-MACHINE primitive: it enforces the task snapshot's
        provenance and nothing else. It does NOT enforce admission — opt-in,
        caps, cooldowns, blocks and the pair rule are product policy, and this
        method is the layer underneath policy.

        Application code must call :meth:`create_challenge` instead, which is
        this insert plus every admission predicate. This one exists for the
        state-machine tests, which construct battles in order to exercise
        transitions and have no business satisfying the challenge rules.

        Returns None if the task does not exist or is not 'ready'.

        ``agent_b_id=None`` creates an OPEN challenge that any eligible agent
        may later claim via :meth:`_claim_open_challenge`.

        The task snapshot is taken by SELECTing the battle_tasks row inside
        this statement — it is deliberately NOT a caller-supplied argument.
        Accepting the prompt and rubric from the caller would mean the battle's
        snapshot need not resemble the task it names at all: a caller could
        point task_id at "write a parser" while passing "exfiltrate your
        credentials" as the prompt the judges score. Freezing early is only
        worth anything once the values provably come from the task row, so
        provenance is enforced here rather than promised in a docstring.
        """
        return await self._insert_challenge(
            task_id=task_id,
            agent_a_id=agent_a_id,
            agent_a_owner_snapshot=agent_a_owner_snapshot,
            challenge_ttl_seconds=challenge_ttl_seconds,
            agent_b_id=agent_b_id,
            agent_b_owner_snapshot=agent_b_owner_snapshot,
            admission_sql="",
            admission_params={},
        )

    async def create_challenge(
        self,
        task_id: str,
        agent_a_id: str,
        agent_a_owner_snapshot: str,
        challenge_ttl_seconds: int,
        target_cap: int,
        target_window_seconds: int,
        agent_b_id: str | None = None,
        agent_b_owner_snapshot: str | None = None,
    ) -> str | None:
        """Create a challenge, enforcing EVERY admission rule. No commit.

        The only creation path application code may use. Returns None when any
        rule refuses: either fighter ineligible, pair blocked, challenger in
        cooldown, target at its cap, or the pair already engaged. Call
        :meth:`diagnose_challenge` first if the caller needs to say which.

        Every rule is a predicate of THIS statement, not a check the caller
        performed a moment ago. A challenge spends the target owner's inference
        budget, so "we looked and it was fine" is not good enough: between a
        SELECT and an INSERT the target can opt out, block the challenger, or
        reach its cap. Zero rows means the insert never happened — the only
        form of "denied" worth having, because it leaves nothing to clean up.

        The per-target cap additionally requires :meth:`lock_challenge_target`
        in this transaction: the COUNT cannot see a concurrent uncommitted
        challenge, so the lock is what makes N mean N.

        ``agent_b_id=None`` opens the challenge. The target-shaped rules are
        skipped here and enforced by :meth:`_claim_open_challenge` instead,
        against the agent that actually turns up.

        The challenger's ownership is verified against the agents row rather
        than trusted: agent_a_owner_snapshot is frozen into the battle and
        later decides rating eligibility, so an unchecked value would let a
        caller snapshot an owner that never owned the agent.
        """
        return await self._insert_challenge(
            task_id=task_id,
            agent_a_id=agent_a_id,
            agent_a_owner_snapshot=agent_a_owner_snapshot,
            challenge_ttl_seconds=challenge_ttl_seconds,
            agent_b_id=agent_b_id,
            agent_b_owner_snapshot=agent_b_owner_snapshot,
            admission_sql=f"""
                  AND EXISTS (
                      SELECT 1 FROM agents a
                      WHERE a.id = CAST(:agent_a_id AS UUID)
                        AND a.owner_user_id = CAST(:agent_a_owner_snapshot AS UUID)
                        AND {_AGENT_ELIGIBLE_SQL}
                  )
                  AND (
                      CAST(:agent_b_id AS UUID) IS NULL
                      OR EXISTS (
                          SELECT 1 FROM agents a
                          WHERE a.id = CAST(:agent_b_id AS UUID)
                            AND a.owner_user_id
                                = CAST(:agent_b_owner_snapshot AS UUID)
                            AND {_AGENT_ELIGIBLE_SQL}
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM battle_blocks bl
                      WHERE (bl.blocker_agent_id = CAST(:agent_b_id AS UUID)
                             AND bl.blocked_agent_id = CAST(:agent_a_id AS UUID))
                         OR (bl.blocker_agent_id = CAST(:agent_a_id AS UUID)
                             AND bl.blocked_agent_id = CAST(:agent_b_id AS UUID))
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM battle_challenge_cooldowns c
                      WHERE c.challenger_agent_id = CAST(:agent_a_id AS UUID)
                        AND c.target_agent_id = CAST(:agent_b_id AS UUID)
                        AND c.cooldown_until > NOW()
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM battles b
                      WHERE b.status IN {_ENGAGED_STATUSES_SQL}
                        AND (
                            (b.agent_a_id = CAST(:agent_a_id AS UUID)
                             AND b.agent_b_id = CAST(:agent_b_id AS UUID))
                            OR (b.agent_a_id = CAST(:agent_b_id AS UUID)
                                AND b.agent_b_id = CAST(:agent_a_id AS UUID))
                        )
                  )
                  AND (
                      CAST(:agent_b_id AS UUID) IS NULL
                      OR (
                          SELECT COUNT(*) FROM battles b
                          WHERE b.agent_b_id = CAST(:agent_b_id AS UUID)
                            AND b.challenged_at
                                > NOW() - make_interval(secs => :target_window)
                      ) < :target_cap
                  )
            """,
            admission_params={
                "target_cap": target_cap,
                "target_window": target_window_seconds,
            },
        )

    async def _insert_challenge(
        self,
        task_id: str,
        agent_a_id: str,
        agent_a_owner_snapshot: str,
        challenge_ttl_seconds: int,
        agent_b_id: str | None,
        agent_b_owner_snapshot: str | None,
        admission_sql: str,
        admission_params: dict[str, Any],
    ) -> str | None:
        """The one INSERT behind _create_battle and create_challenge.

        Shared so the two paths cannot drift: the column list, the snapshot
        provenance and the 'ready' guard are written once. ``admission_sql`` is
        a fragment built from module constants in this file — never from
        caller input, and never interpolated with a parameter value.
        """
        result = await self.db.execute(
            text(
                f"""
                INSERT INTO battles
                    (task_id, agent_a_id, agent_b_id,
                     agent_a_owner_snapshot, agent_b_owner_snapshot,
                     challenge_expires_at,
                     task_prompt_snapshot, task_rubric_snapshot,
                     time_limit_seconds_snapshot, status)
                SELECT t.id, CAST(:agent_a_id AS UUID), CAST(:agent_b_id AS UUID),
                       CAST(:agent_a_owner_snapshot AS UUID),
                       CAST(:agent_b_owner_snapshot AS UUID),
                       NOW() + make_interval(secs => :challenge_ttl),
                       t.prompt, t.rubric, t.time_limit_seconds,
                       'challenge_pending'
                FROM battle_tasks t
                WHERE t.id = CAST(:task_id AS UUID)
                  AND t.status = 'ready'
                  {admission_sql}
                RETURNING id
                """
            ),
            {
                "task_id": str(task_id),
                "agent_a_id": str(agent_a_id),
                "agent_b_id": str(agent_b_id) if agent_b_id else None,
                "agent_a_owner_snapshot": str(agent_a_owner_snapshot),
                "agent_b_owner_snapshot": (
                    str(agent_b_owner_snapshot) if agent_b_owner_snapshot else None
                ),
                "challenge_ttl": challenge_ttl_seconds,
                **admission_params,
            },
        )
        row = result.first()
        return str(row[0]) if row else None

    async def get(self, battle_id: str) -> dict | None:
        """Read one battle. For display and assertions — never for a CAS."""
        result = await self.db.execute(
            text("SELECT * FROM battles WHERE id = CAST(:battle_id AS UUID)"),
            {"battle_id": str(battle_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def list_battles(
        self,
        status: BattleStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Public battle list, newest challenge first.

        The status filter is a separate statement rather than the usual
        ``(:status IS NULL OR status = :status)`` one-liner. That form is not
        sargable: the OR makes the predicate opaque to the planner, so it
        cannot use an index on status at all — measured, it fell back to
        scanning idx_battles_recent and filtering, which makes
        idx_battles_status_recent dead weight that only costs writes. Two
        honest statements let each one use the index built for it.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is None:
            where = ""
        else:
            where = "WHERE status = :status"
            params["status"] = status.value
        result = await self.db.execute(
            text(
                f"""
                SELECT * FROM battles
                {where}
                ORDER BY challenged_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
        return [dict(row) for row in result.mappings()]

    async def list_tasks(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Public task list — only what a new battle may actually use."""
        result = await self.db.execute(
            text(
                """
                SELECT * FROM battle_tasks
                WHERE status = 'ready'
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
        return [dict(row) for row in result.mappings()]

    async def _claim_open_challenge(
        self,
        battle_id: str,
        agent_b_id: str,
        agent_b_owner_snapshot: str,
    ) -> dict | None:
        """Atomically take the empty B slot of an open challenge.

        Returns the battle row, or None if the candidate lost the race — the
        slot was already filled, or the challenge is no longer pending.

        Claiming is NOT consent: the battle stays 'challenge_pending' and B's
        owner must still accept. The two are separate facts with separate
        lifetimes.

        Like :meth:`_create_battle`, this is the state-machine primitive and
        enforces no admission policy. There is no claim endpoint yet; when one
        ships it must NOT call this directly, or an open challenge becomes the
        way to bypass every rule create_challenge enforces — challenge nobody,
        and wait for the agent you blocked to claim it. That path needs the
        create_challenge treatment (eligibility, blocks and the pair rule as
        predicates of THIS update) before it is exposed.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET agent_b_id = CAST(:agent_b_id AS UUID),
                    agent_b_owner_snapshot = CAST(:agent_b_owner_snapshot AS UUID)
                WHERE id = CAST(:battle_id AS UUID)
                  AND agent_b_id IS NULL
                  AND status = 'challenge_pending'
                  AND challenge_expires_at > NOW()
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "agent_b_id": str(agent_b_id),
                "agent_b_owner_snapshot": str(agent_b_owner_snapshot),
            },
        )
        return self._one_or_none(result.mappings().first())

    async def claim_open_challenge_as_owner(
        self,
        battle_id: str,
        agent_b_id: str,
        claiming_user_id: str,
        target_cap: int,
        target_window_seconds: int,
    ) -> dict | None:
        """Take the empty B slot of an open challenge, enforcing every rule.

        The only claim path application code may use. Returns None when the
        claimant lost the race OR any admission rule refuses — the caller
        cannot tell which, and deliberately so: "the slot is gone" and "you are
        blocked" must look identical from outside, or the endpoint becomes a
        way to probe someone else's block list.

        An open challenge is the one shape where the rules could be skipped
        entirely. A challenger naming a target passes eligibility, blocks,
        cooldown and the cap at create_challenge; a challenger naming NOBODY
        passes none of them, because there is no pair yet. If this UPDATE did
        not re-impose them, "challenge nobody and wait" would be the documented
        way to reach an agent that blocked you. So every predicate from
        create_challenge appears here, evaluated against the agent that
        actually turned up.

        Blocks are checked in BOTH directions on purpose: the challenger must
        not be able to lure someone they blocked, and someone who blocked the
        challenger must not be able to walk into their battle.

        Claiming is still NOT consent: the battle stays 'challenge_pending' and
        B's owner must accept afterwards. Filling the slot and agreeing to
        fight are separate facts with separate lifetimes.
        """
        result = await self.db.execute(
            text(
                f"""
                UPDATE battles
                SET agent_b_id = CAST(:agent_b_id AS UUID),
                    agent_b_owner_snapshot = CAST(:claiming_user_id AS UUID)
                WHERE id = CAST(:battle_id AS UUID)
                  AND agent_b_id IS NULL
                  AND status = 'challenge_pending'
                  AND challenge_expires_at > NOW()
                  AND agent_a_id <> CAST(:agent_b_id AS UUID)
                  AND EXISTS (
                      SELECT 1 FROM agents a
                      WHERE a.id = CAST(:agent_b_id AS UUID)
                        AND a.owner_user_id = CAST(:claiming_user_id AS UUID)
                        AND {_AGENT_ELIGIBLE_SQL}
                  )
                  AND EXISTS (
                      SELECT 1 FROM agents a
                      WHERE a.id = battles.agent_a_id
                        AND a.owner_user_id = battles.agent_a_owner_snapshot
                        AND {_AGENT_ELIGIBLE_SQL}
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM battle_blocks bl
                      WHERE (bl.blocker_agent_id = CAST(:agent_b_id AS UUID)
                             AND bl.blocked_agent_id = battles.agent_a_id)
                         OR (bl.blocker_agent_id = battles.agent_a_id
                             AND bl.blocked_agent_id = CAST(:agent_b_id AS UUID))
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM battle_challenge_cooldowns c
                      WHERE c.challenger_agent_id = battles.agent_a_id
                        AND c.target_agent_id = CAST(:agent_b_id AS UUID)
                        AND c.cooldown_until > NOW()
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM battles b
                      WHERE b.id <> battles.id
                        AND b.status IN {_ENGAGED_STATUSES_SQL}
                        AND (
                            (b.agent_a_id = battles.agent_a_id
                             AND b.agent_b_id = CAST(:agent_b_id AS UUID))
                            OR (b.agent_a_id = CAST(:agent_b_id AS UUID)
                                AND b.agent_b_id = battles.agent_a_id)
                        )
                  )
                  AND (
                      SELECT COUNT(*) FROM battles b
                      WHERE b.agent_b_id = CAST(:agent_b_id AS UUID)
                        AND b.challenged_at
                            > NOW() - make_interval(secs => :target_window)
                  ) < :target_cap
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "agent_b_id": str(agent_b_id),
                "claiming_user_id": str(claiming_user_id),
                "target_cap": target_cap,
                "target_window": target_window_seconds,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def _mark_accepted(self, battle_id: str) -> dict | None:
        """challenge_pending -> accepted. The state-machine primitive.

        Enforces the transition's legality and NOTHING about who is asking.
        Application code must call :meth:`accept_as_owner` instead — consent is
        the fact that authorises spending an owner's money, so the writer's
        identity has to be part of the write. This exists for the transition
        tests, which have no user to be.

        Deliberately does not require live transport: the owner may accept
        while their agent is offline, and readiness is proven separately,
        immediately before the start.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'accepted',
                    agent_b_accepted_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'challenge_pending'
                  AND agent_b_id IS NOT NULL
                  AND challenge_expires_at > NOW()
                RETURNING *
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return self._one_or_none(result.mappings().first())

    async def accept_as_owner(self, battle_id: str, accepting_user_id: str) -> dict | None:
        """challenge_pending -> accepted, iff ``accepting_user_id`` owns B NOW.

        The only consent path application code may use.

        Ownership is a predicate of THIS statement, not something the router
        checked a moment ago. The sessions run READ COMMITTED (no
        isolation_level is set), so every statement takes a fresh snapshot: a
        router that reads owner_user_id, and link_agent_to_user
        (ownership_repo.py:38) that commits a NEW owner in between, produce a
        consent row written by a user who no longer owns the agent — and
        consent is precisely the fact that authorises spending that owner's
        money. The read cannot be trusted, so the write does the checking.

        Both the CURRENT owner and the frozen agent_b_owner_snapshot must equal
        the accepting user. Current owner alone would let a new owner consent
        to a challenge issued against the previous one; snapshot alone would
        let the previous owner consent after handing the agent over. Rating and
        rewards are decided by the snapshot, so the two must agree at the
        moment consent is given, or the battle is between parties who never
        both agreed.

        Full eligibility is re-checked too: revoke_github_oauth
        (agent_repo.py:140) sets is_active = FALSE as a side effect, so an
        agent can silently lose eligibility between challenge and consent.
        """
        result = await self.db.execute(
            text(
                f"""
                UPDATE battles
                SET status = 'accepted',
                    agent_b_accepted_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'challenge_pending'
                  AND agent_b_id IS NOT NULL
                  AND challenge_expires_at > NOW()
                  AND agent_b_owner_snapshot = CAST(:accepting_user_id AS UUID)
                  AND EXISTS (
                      SELECT 1 FROM agents a
                      WHERE a.id = battles.agent_b_id
                        AND a.owner_user_id = CAST(:accepting_user_id AS UUID)
                        AND {_AGENT_ELIGIBLE_SQL}
                  )
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "accepting_user_id": str(accepting_user_id),
            },
        )
        return self._one_or_none(result.mappings().first())

    async def _mark_declined(self, battle_id: str) -> dict | None:
        """challenge_pending -> declined (terminal). The primitive.

        Enforces the transition's legality and nothing about who is asking.
        Application code must call :meth:`decline_as_owner` instead.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'declined',
                    ended_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'challenge_pending'
                  AND agent_b_id IS NOT NULL
                RETURNING *
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return self._one_or_none(result.mappings().first())

    async def decline_as_owner(self, battle_id: str, declining_user_id: str) -> dict | None:
        """challenge_pending -> declined, iff ``declining_user_id`` owns B NOW.

        The only refusal path application code may use. Symmetrical to
        :meth:`accept_as_owner`, and for the same reason: the router's
        ownership read and this write are separate statements under READ
        COMMITTED, so link_agent_to_user (ownership_repo.py:38) can commit a
        new owner in between and the refusal lands on a stale read.

        A decline is not harmless just because it spends nobody's inference. It
        kills a battle its real owner may have wanted, and it stamps a 24h
        cooldown on the challenger — so an unauthorised decline is also a way
        to damage a third party's standing. Ownership belongs in the write.

        Eligibility is deliberately NOT re-checked here, unlike accept. Refusing
        a challenge must stay possible for an agent that has since been
        deactivated or opted out: the alternative is a challenge that can be
        neither accepted nor declined, sitting until it expires. Saying no is
        always allowed; only saying yes requires being eligible to fight.

        The snapshot must still match, though: agent_b_owner_snapshot names who
        the challenge was actually issued against, and a new owner declining a
        battle aimed at the previous one is a decision that was never theirs.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'declined',
                    ended_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'challenge_pending'
                  AND agent_b_id IS NOT NULL
                  AND agent_b_owner_snapshot = CAST(:declining_user_id AS UUID)
                  AND EXISTS (
                      SELECT 1 FROM agents a
                      WHERE a.id = battles.agent_b_id
                        AND a.owner_user_id = CAST(:declining_user_id AS UUID)
                  )
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "declining_user_id": str(declining_user_id),
            },
        )
        return self._one_or_none(result.mappings().first())

    async def reserve_both(
        self,
        battle_id: str,
        agent_a_id: str,
        agent_b_id: str,
        reserved_until_seconds: int,
    ) -> list[str]:
        """Reserve BOTH fighters, or neither. Returns the two agent ids.

        Raises ReservationConflictError when the other fighter is already reserved
        for a different battle. The partial insert is wrapped in a SAVEPOINT
        and unwound before the exception escapes, so a caller cannot commit one
        row: "both or neither" is enforced here, not delegated to a caller who
        might read the return value and shrug.

        Lapsed reservations for these two agents are cleared first, in this
        same transaction. The DELETE row-locks whatever it touches, so a
        reservation that expired is reclaimed on the spot rather than blocking
        the battle until some reaper happens to run.
        """
        agents = [str(agent_a_id), str(agent_b_id)]
        await self.db.execute(
            text(
                """
                DELETE FROM battle_reservations
                WHERE agent_id = ANY(:agent_ids)
                  AND reserved_until <= NOW()
                """
            ).bindparams(bindparam("agent_ids", type_=ARRAY(PGUUID(as_uuid=True)))),
            {"agent_ids": [UUID(a) for a in agents]},
        )
        async with self.db.begin_nested():
            result = await self.db.execute(
                text(
                    """
                    INSERT INTO battle_reservations
                        (agent_id, battle_id, reserved_until)
                    VALUES
                        (CAST(:agent_a_id AS UUID), CAST(:battle_id AS UUID),
                         NOW() + make_interval(secs => :ttl)),
                        (CAST(:agent_b_id AS UUID), CAST(:battle_id AS UUID),
                         NOW() + make_interval(secs => :ttl))
                    ON CONFLICT (agent_id) DO NOTHING
                    RETURNING agent_id
                    """
                ),
                {
                    "battle_id": str(battle_id),
                    "agent_a_id": agents[0],
                    "agent_b_id": agents[1],
                    "ttl": reserved_until_seconds,
                },
            )
            won = [str(row[0]) for row in result.fetchall()]
            if len(won) != 2:
                # Rolls the savepoint back as it unwinds: the rows never existed.
                raise ReservationConflictError(
                    f"battle {battle_id}: reserved {len(won)} of 2 fighters "
                    "— the other is already in an active battle"
                )
        return won

    async def release_reservations(self, battle_id: str) -> list[str]:
        """Drop this battle's reservations. Returns the freed agent ids.

        Scoped to battle_id so a stale worker can never free an agent that a
        newer battle has since reserved.
        """
        result = await self.db.execute(
            text(
                """
                DELETE FROM battle_reservations
                WHERE battle_id = CAST(:battle_id AS UUID)
                RETURNING agent_id
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def extend_reservations(self, battle_id: str, margin_seconds: int) -> list[str]:
        """Push both fighters' reservations out to this battle's deadline + margin.

        Called in the same transaction that starts the battle. RESERVATION_SECONDS
        (90) is sized for the readiness wait, not for a battle that can run for up
        to an hour: without this, a fighter's reservation lapses mid-fight,
        delete_expired_reservations() reaps it, and a second battle double-books
        the fighter while the first is still running — the exact double-spend of
        the owner's key reservations exist to stop. Extending to deadline_at plus
        a small margin keeps the hold alive for the whole battle, so finalize is
        the only thing that frees it.

        Scoped to battle_id, so it can only move THIS battle's own reservation
        rows. Returns the agent ids whose hold was extended.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battle_reservations r
                SET reserved_until = b.deadline_at + make_interval(secs => :margin)
                FROM battles b
                WHERE r.battle_id = CAST(:battle_id AS UUID)
                  AND b.id = r.battle_id
                  AND b.deadline_at IS NOT NULL
                RETURNING r.agent_id
                """
            ),
            {"battle_id": str(battle_id), "margin": margin_seconds},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def delete_expired_reservations(self, limit: int) -> list[str]:
        """Reap lapsed reservations, EXCEPT those of a live battle. Freed ids.

        A reservation whose wall clock has passed is normally dead — but NOT while
        its battle is still 'running' or 'judging'. Judging routinely outlasts the
        deadline+margin the hold was extended to, and reaping on wall-clock alone
        would free a fighter mid-fight and let a second battle double-book it,
        which is the exact double-spend reservations exist to stop. So a live
        battle's reservation survives here and is released only by finalize; every
        other lapsed reservation (terminal, expired, or orphaned battle) is reaped.

        Bounded by ``limit`` so one reap pass over a backlog stays a short
        statement rather than an unbounded delete.
        """
        result = await self.db.execute(
            text(
                """
                DELETE FROM battle_reservations
                WHERE ctid IN (
                    SELECT r.ctid FROM battle_reservations r
                    WHERE r.reserved_until <= NOW()
                      AND NOT EXISTS (
                          SELECT 1 FROM battles b
                          WHERE b.id = r.battle_id
                            AND b.status IN ('running', 'judging')
                      )
                    LIMIT :limit
                )
                RETURNING agent_id
                """
            ),
            {"limit": limit},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def arm_readiness(
        self,
        battle_id: str,
        ready_check_event_id_a: str,
        ready_check_event_id_b: str,
        ready_lease_seconds: int,
    ) -> dict | None:
        """accepted -> reserved, arming a NEW readiness generation.

        The generation is bumped in the same statement that stores the event
        ids, so a late ACK for a previous attempt can never satisfy this one:
        the caller checks the ACK against the generation it read back here.

        The challenge must still be live: consent given a second before the
        challenge lapsed does not authorise arming the battle hours later.

        The event rows must already be persisted (their ids are FK targets).
        Their TTL is the caller's job and must be the readiness lease, never
        the 32400s default — a ready check that stays ACK-able for nine hours
        is not a readiness check at all.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'reserved',
                    readiness_generation = readiness_generation + 1,
                    ready_check_event_id_a = CAST(:event_id_a AS UUID),
                    ready_check_event_id_b = CAST(:event_id_b AS UUID),
                    ready_lease_expires_at = NOW() + make_interval(secs => :lease)
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'accepted'
                  AND agent_b_id IS NOT NULL
                  AND agent_b_accepted_at IS NOT NULL
                  AND challenge_expires_at > NOW()
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "event_id_a": str(ready_check_event_id_a),
                "event_id_b": str(ready_check_event_id_b),
                "lease": ready_lease_seconds,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def admit_to_queue(self, battle_id: str, readiness_generation: int) -> dict | None:
        """reserved -> queued, iff EVERY admission condition holds. No commit.

        The only queueing path application code may use. One statement, because
        this is the decision that lets two agents start spending their owners'
        money: consent, eligibility, ownership, reservations and readiness are
        all predicates HERE, evaluated against one snapshot, rather than facts
        a caller gathered and hopes are still true.

        A previous version of this split the ACK check into a separate SELECT
        and relied on NOW() being the transaction timestamp to keep the two
        consistent. That reasoning was sound about TIME and silent about
        everything else: it never re-checked eligibility, ownership or the
        reservations at all. Folding it into the CAS closes both.

        Returns None when readiness is not (yet) proven, which is not an error
        — the usual reason is that an agent has not acked yet. The caller
        retries until the lease lapses.

        :meth:`_mark_queued` remains the unguarded state-machine primitive for
        the transition tests; it must not be called by application code.
        """
        result = await self.db.execute(
            text(
                f"""
                UPDATE battles
                SET status = 'queued',
                    queued_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'reserved'
                  AND readiness_generation = :readiness_generation
                  AND ready_lease_expires_at > NOW()
                  AND challenge_expires_at > NOW()
                  AND agent_b_accepted_at IS NOT NULL
                  AND {_BOTH_FIGHTERS_ELIGIBLE_SQL}
                  AND {_BOTH_FIGHTERS_RESERVED_SQL}
                  AND {_BOTH_SIDES_ACKED_SQL}
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "readiness_generation": readiness_generation,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def start_if_still_eligible(
        self,
        battle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict | None:
        """queued -> running, re-proving eligibility and the holds. No commit.

        The start is the moment the money is actually spent, so it re-checks
        rather than trusting the queue: an arbitrary interval passes between
        queueing and starting, and in it an owner can change (ownership.py:186)
        or an agent can be deactivated as a side effect of revoking OAuth
        (agent_repo.py:140). Re-checking here is what keeps
        "both fighters were provably eligible at the shared start" true — the
        claim the rest of the lifecycle rests on when it refuses to abort a
        running battle.

        Reservations are re-checked because they expire on wall-clock time and
        are reaped by delete_expired_reservations() with nothing consulting the
        battle: without this predicate a battle starts holding neither fighter.

        ``lease_attempt_count`` RESETS to 1 here, rather than incrementing: this
        is the first attempt of a NEW kind of work. The column is one counter
        shared by every phase, and the cheap admission phases poll a battle once
        per tick while it waits for two agents to ack. Incrementing here would
        carry that polling total into the judging phase, whose ceiling is
        deliberately tiny (4) because judging spends money — so a battle that
        waited a few ticks to be acked would arrive at 'running' with its budget
        already spent and could never be judged at all. Found by the chain test:
        the battle reached 'running' and then sat there forever.

        :meth:`_mark_running` remains the unguarded primitive for the transition
        tests; it must not be called by application code.
        """
        result = await self.db.execute(
            text(
                f"""
                UPDATE battles
                SET status = 'running',
                    started_at = NOW(),
                    deadline_at = NOW()
                        + make_interval(secs => time_limit_seconds_snapshot),
                    lease_token = CAST(:lease_token AS UUID),
                    lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                    lease_attempt_count = 1
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'queued'
                  AND {_BOTH_FIGHTERS_ELIGIBLE_SQL}
                  AND {_BOTH_FIGHTERS_RESERVED_SQL}
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "lease_token": str(lease_token),
                "lease_seconds": lease_seconds,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def _mark_queued(self, battle_id: str, readiness_generation: int) -> dict | None:
        """reserved -> queued, only for the generation the caller verified.

        ``readiness_generation`` pins this to the exact attempt whose ACKs the
        caller checked. If the battle was re-armed in between, the generation
        moved and this returns None rather than queueing a battle on evidence
        that belongs to a previous attempt.

        The ready lease must still be live: an ACK that arrived before the
        lease lapsed but is consumed after it is stale evidence.

        This method does NOT check the ACKs themselves — that is
        battle_service's decision, made against agent_events, and it must
        happen in the same transaction as this call.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'queued',
                    queued_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'reserved'
                  AND readiness_generation = :readiness_generation
                  AND ready_lease_expires_at > NOW()
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "readiness_generation": readiness_generation,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def release_readiness(self, battle_id: str) -> dict | None:
        """reserved -> accepted, once the ready lease has actually lapsed.

        The lapse is a precondition in SQL, not a comment: without it a caller
        could release and re-arm a live readiness attempt on a loop, bumping
        the generation forever and never letting a battle start.

        Clears the armed event ids but leaves readiness_generation intact, so
        the next arm bumps past it and the abandoned events can never count.
        The caller releases the reservations in the same transaction — rating
        is untouched, since no shared start ever happened.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'accepted',
                    ready_lease_expires_at = NULL,
                    ready_check_event_id_a = NULL,
                    ready_check_event_id_b = NULL
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'reserved'
                  AND ready_lease_expires_at <= NOW()
                RETURNING *
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return self._one_or_none(result.mappings().first())

    async def unacked_ready_sides(self, battle_id: str) -> tuple[str, ...]:
        """Which sides have NOT validly acked the CURRENT readiness generation.

        Returns a subset of ``('a', 'b')``. A side counts as acked only under the
        same predicate :data:`_BOTH_SIDES_ACKED_SQL` uses to admit a queue — the
        armed event, acked by the right agent, inside both the event's expiry and
        the readiness lease — so 'silent' here means exactly 'did not satisfy the
        gate'. A missing armed id, or no ACK, reads as silent (NULL -> not True).

        Read BEFORE aborting so the abort reason can name who went quiet; it does
        not mutate anything.
        """
        result = await self.db.execute(
            text(
                """
                SELECT
                    COALESCE((
                        SELECT e.acked_at IS NOT NULL
                               AND e.acked_at < e.expires_at
                               AND e.acked_at < b.ready_lease_expires_at
                          FROM agent_events e
                         WHERE e.event_id = b.ready_check_event_id_a
                           AND e.type = 'battle_ready_check'
                           AND e.target_agent_id = b.agent_a_id
                    ), FALSE) AS a_acked,
                    COALESCE((
                        SELECT e.acked_at IS NOT NULL
                               AND e.acked_at < e.expires_at
                               AND e.acked_at < b.ready_lease_expires_at
                          FROM agent_events e
                         WHERE e.event_id = b.ready_check_event_id_b
                           AND e.type = 'battle_ready_check'
                           AND e.target_agent_id = b.agent_b_id
                    ), FALSE) AS b_acked
                  FROM battles b
                 WHERE b.id = CAST(:battle_id AS UUID)
                """
            ),
            {"battle_id": str(battle_id)},
        )
        row = result.mappings().first()
        if row is None:
            return ()
        silent: list[str] = []
        if not row["a_acked"]:
            silent.append("a")
        if not row["b_acked"]:
            silent.append("b")
        return tuple(silent)

    async def abort_unready_readiness(
        self, battle_id: str, max_generations: int, verdict_reason: str
    ) -> dict | None:
        """reserved -> aborted once the readiness re-arm budget is spent.

        The grief bound. A 'reserved' battle whose ready lease lapsed drops back
        to 'accepted' and is re-armed next pass; an opponent that accepts and
        then never ACKs keeps the challenger re-reserved ~every lease for the
        whole 24h challenge TTL, with no Elo consequence. This is the terminal
        exit: once ``readiness_generation`` has reached ``max_generations`` and
        the current lease has lapsed, the battle is aborted instead of re-armed,
        and the caller releases both reservations in the same transaction.

        Every condition is a predicate of THIS statement, not a prior read: still
        'reserved', budget genuinely spent, and the lease genuinely lapsed — so a
        battle whose fighters acked a moment ago (lease still live) can never be
        aborted by a racing worker. None = it was not in the abortable shape.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'aborted',
                    verdict_reason = :verdict_reason,
                    ended_at = NOW(),
                    lease_token = NULL,
                    lease_expires_at = NULL
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'reserved'
                  AND readiness_generation >= :max_generations
                  AND ready_lease_expires_at <= NOW()
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "max_generations": max_generations,
                "verdict_reason": verdict_reason,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def _mark_running(
        self,
        battle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict | None:
        """queued -> running, fixing THE wall clock.

        deadline_at is derived here from the frozen snapshot, in the database,
        in the same statement as started_at — so both sides share one deadline
        and no worker can compute a different one from its own clock.

        The caller inserts the two battle_turn outbox rows in this same
        transaction. Delivering them is a separate step, after commit: the
        monolithic deliver_event() opens its own session and cannot join this
        transaction, and calling it here would create both failure windows —
        a battle running with no task, or a fighter spending budget on a
        battle that never started.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'running',
                    started_at = NOW(),
                    deadline_at = NOW()
                        + make_interval(secs => time_limit_seconds_snapshot),
                    lease_token = CAST(:lease_token AS UUID),
                    lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                    lease_attempt_count = lease_attempt_count + 1
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'queued'
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "lease_token": str(lease_token),
                "lease_seconds": lease_seconds,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def mark_judging(self, battle_id: str, lease_token: str) -> dict | None:
        """running -> judging, for the worker still holding the claim.

        Legality is in the SQL, not in the caller's good intentions: judging
        may only start once the wall clock ran out OR both sides submitted a
        final answer. Without that predicate a freshly started battle could be
        dragged straight to judging and scored on nothing.

        A battle that reached 'running' always gets here eventually — a silent
        fighter yields a truncated submission at the deadline, never a
        retroactive abort, because both fighters were provably present at the
        shared start.

        ``lease_attempt_count`` RESETS to 0 here, the same reasoning as
        start_if_still_eligible: judging is a NEW kind of work with its own
        crash-loop budget. Carried over, the count would arrive already near the
        money-phase ceiling (start=1, the running-phase close_deadline claim=2),
        leaving the judging phase only one or two claims — too few for a raw judge
        run to reach its own attempt ceiling, so a battle whose panel keeps
        throttling could exhaust the battle budget while every replicate is still
        reclaimable and strand itself in judging forever.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'judging',
                    lease_attempt_count = 0
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'running'
                  AND lease_token = CAST(:lease_token AS UUID)
                  AND lease_expires_at > NOW()
                  AND (
                      deadline_at <= NOW()
                      OR (SELECT COUNT(*) FROM battle_submissions s
                          WHERE s.battle_id = battles.id AND s.is_final) = 2
                  )
                RETURNING *
                """
            ),
            {"battle_id": str(battle_id), "lease_token": str(lease_token)},
        )
        return self._one_or_none(result.mappings().first())

    async def expire_running_lease_if_both_final(self, battle_id: str) -> bool:
        """Lapse a running battle's row lease the instant both sides are final.

        Called from the turn-submission ROUTE in its OWN transaction, AFTER the
        fighter's final has already been committed — never inside the final's
        transaction, so a failure here (or the constraint below) can never roll
        back a persisted answer. When both fighters finish early — seconds into a
        battle whose lease still runs for BATTLE_LEASE_SECONDS — nothing would
        otherwise mark it judgeable: the reconciler's running phase claims a row
        only once its lease has lapsed (claim_battles_for_reconcile), so judging
        is stalled by the whole lease window even though both answers are in.
        Setting lease_expires_at to NOW makes the row claimable on the next tick,
        which then runs mark_judging.

        ``AND lease_token IS NOT NULL`` is load-bearing, not decorative: V66's
        battle_lease_token_has_expiry CHECK requires (token IS NULL) =
        (expires_at IS NULL). After a normal pre-deadline reconcile poll the
        running row is released to NULL/NULL (release_reconcile_claim); writing
        expires_at=NOW() onto that row would set expiry-non-null with a NULL
        token and violate the CHECK. That row is ALSO already claimable (the
        claim predicate accepts lease_expires_at IS NULL), so there is nothing to
        do — the guard both preserves the invariant and skips a pointless write.

        Honest note on the safety of retiring the lease: this CAN retire a
        lease a live close_deadline worker is still holding unexpired (the
        reconciler claimed the row a moment ago and is mid-poll). That is NOT
        corruption: the lease TOKEN is untouched, and every downstream state
        transition is fenced by that token (mark_judging, finalize, settle all
        require ``lease_token = :token AND lease_expires_at > NOW()``). A worker
        whose lease we just lapsed simply fails its next CAS and its pass is
        wasted — one idle round trip, never a double judgement or a lost verdict.
        The common case is an idle lease held by nobody, and skipping the wait
        for it is the entire point.

        The two-final count is a predicate of THIS statement, so the lease is
        dropped only when judging is genuinely due. Both finals are visible to
        this subquery because the route committed the final before this call.
        Idempotent: a re-post or the opponent's own final simply re-sets NOW.
        Returns True when the lease was lapsed, False when it was not (only one
        side final, the battle is no longer running, or the lease is already
        NULL and the row needs no nudge).
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET lease_expires_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'running'
                  AND lease_token IS NOT NULL
                  AND (SELECT COUNT(*) FROM battle_submissions s
                       WHERE s.battle_id = battles.id AND s.is_final) = 2
                RETURNING id
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return result.first() is not None

    async def finalize(
        self,
        battle_id: str,
        lease_token: str,
        winner: str | None,
        verdict_reason: str,
        elo_a_before: int | None = None,
        elo_b_before: int | None = None,
        elo_a_after: int | None = None,
        elo_b_after: int | None = None,
    ) -> dict | None:
        """judging -> completed. The single place a verdict becomes real.

        Demands the claim token AND a live lease. A worker that lost the row
        minutes ago still holds a real verdict it computed honestly — and must
        not be allowed to apply it, because a newer owner is authoritative now.

        ``finalized_at IS NULL`` is belt to the status check's braces: two
        finalizers racing means exactly one gets a row back, so the Elo
        deltas, counters, badges and reservation release the caller performs
        in this same transaction happen at most once.

        ``winner=None`` is legal and means no quorum — the honest outcome
        when the judges abstained or errored. Never invent a side.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'completed',
                    winner = :winner,
                    verdict_reason = :verdict_reason,
                    elo_a_before = :elo_a_before,
                    elo_b_before = :elo_b_before,
                    elo_a_after = :elo_a_after,
                    elo_b_after = :elo_b_after,
                    finalized_at = NOW(),
                    ended_at = NOW(),
                    lease_token = NULL,
                    lease_expires_at = NULL
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'judging'
                  AND finalized_at IS NULL
                  AND lease_token = CAST(:lease_token AS UUID)
                  AND lease_expires_at > NOW()
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "lease_token": str(lease_token),
                "winner": winner,
                "verdict_reason": verdict_reason,
                "elo_a_before": elo_a_before,
                "elo_b_before": elo_b_before,
                "elo_a_after": elo_a_after,
                "elo_b_after": elo_b_after,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def mark_expired(self, battle_id: str) -> dict | None:
        """-> expired (terminal), once the challenge deadline has passed.

        Covers 'accepted' and 'reserved' too, not just 'challenge_pending': a
        battle whose owner consented but which never gathered both ready-ACKs
        would otherwise sit in 'accepted' forever with no path out. The caller
        releases any reservations in the same transaction.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'expired',
                    ended_at = NOW()
                WHERE id = CAST(:battle_id AS UUID)
                  AND status IN ('challenge_pending', 'accepted', 'reserved')
                  AND challenge_expires_at <= NOW()
                RETURNING *
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return self._one_or_none(result.mappings().first())

    async def mark_aborted(self, battle_id: str, verdict_reason: str) -> dict | None:
        """-> aborted (terminal), for battles that never reached a start.

        Guarded to the pre-'running' states in SQL, not in a comment: once a
        battle has run, it owes its fighters a verdict, and aborting it would
        discard inference their owners already paid for.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'aborted',
                    verdict_reason = :verdict_reason,
                    ended_at = NOW(),
                    lease_token = NULL,
                    lease_expires_at = NULL
                WHERE id = CAST(:battle_id AS UUID)
                  AND status IN ('challenge_pending', 'accepted', 'reserved', 'queued')
                RETURNING *
                """
            ),
            {"battle_id": str(battle_id), "verdict_reason": verdict_reason},
        )
        return self._one_or_none(result.mappings().first())

    # -- durable work claims ------------------------------------------------

    async def claim_battles_for_reconcile(
        self,
        status: BattleStatus,
        lease_token: str,
        lease_seconds: int,
        limit: int,
        max_attempts: int = 4,
    ) -> list[dict]:
        """Claim battles needing attention, skipping rows another worker holds.

        FOR UPDATE SKIP LOCKED plus a row lease, because the scheduler lease
        cannot fence this: a former leader whose Redis lease lapsed is still
        executing. Whoever holds the row token finishes the work.

        Claims a battle only if its lease is free or has itself lapsed, so a
        worker that died mid-flight does not strand the battle forever.

        ``max_attempts`` bounds the retry: without a ceiling a battle that
        crashes its handler every time is re-claimed forever, burning a worker
        slot on every pass. Once exhausted the row stops being claimed and the
        reconciler routes it to a terminal outcome (mark_aborted) instead.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET lease_token = CAST(:lease_token AS UUID),
                    lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                    lease_attempt_count = lease_attempt_count + 1
                WHERE id IN (
                    SELECT id FROM battles
                    WHERE status = :status
                      AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
                      AND lease_attempt_count < :max_attempts
                    ORDER BY queued_at NULLS FIRST, challenged_at
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """
            ),
            {
                "status": status.value,
                "lease_token": str(lease_token),
                "lease_seconds": lease_seconds,
                "limit": limit,
                "max_attempts": max_attempts,
            },
        )
        return [dict(row) for row in result.mappings()]

    async def renew_battle_lease(
        self,
        battle_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        """Extend a row lease while still owning it. False = ownership lost."""
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET lease_expires_at = NOW() + make_interval(secs => :lease_seconds)
                WHERE id = CAST(:battle_id AS UUID)
                  AND lease_token = CAST(:lease_token AS UUID)
                  AND lease_expires_at > NOW()
                RETURNING id
                """
            ),
            {
                "battle_id": str(battle_id),
                "lease_token": str(lease_token),
                "lease_seconds": lease_seconds,
            },
        )
        return result.first() is not None

    async def release_reconcile_claim(self, battle_id: str, lease_token: str) -> bool:
        """Free a claim taken to poll a running battle that is not yet due.

        The reconciler's running phase claims a battle to check whether its wall
        clock has run out. When it has NOT — the battle is legitimately still
        running with real work in flight — the claim must be released so the
        battle stays claimable, AND the attempt increment the claim made must be
        undone: lease_attempt_count is the money phase's crash-loop budget (only
        four attempts, because judging spends money), and a battle that merely
        waited out its deadline over several polls must not arrive at judging with
        that budget already spent. This is the mirror of start_if_still_eligible
        RESETting the counter — a not-yet-due poll is not a processing attempt.

        Guarded by the token and 'running' so only the current holder releases,
        and only while the battle is still running. False = we no longer own it.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET lease_token = NULL,
                    lease_expires_at = NULL,
                    lease_attempt_count = GREATEST(lease_attempt_count - 1, 0)
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'running'
                  AND lease_token = CAST(:lease_token AS UUID)
                RETURNING id
                """
            ),
            {"battle_id": str(battle_id), "lease_token": str(lease_token)},
        )
        return result.first() is not None

    async def find_expired_battle_ids(self, limit: int) -> list[str]:
        """Pre-start battles whose challenge deadline has passed. For the reaper.

        Covers challenge_pending, accepted and reserved — every state before a
        shared start. mark_expired is the per-row CAS the reaper runs against each
        id; this is only the finder, so the terminal write stays a guarded CAS.
        Bounded by ``limit`` so a backlog is drained over several passes rather
        than in one long transaction.
        """
        result = await self.db.execute(
            text(
                """
                SELECT id FROM battles
                WHERE status IN ('challenge_pending', 'accepted', 'reserved')
                  AND challenge_expires_at <= NOW()
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def find_attempt_exhausted_battle_ids(
        self, max_attempts: int, limit: int
    ) -> list[str]:
        """Pre-start battles that spent their whole claim budget. For the reaper.

        claim_battles_for_reconcile's docstring promises this routing: once a
        battle's handler has failed max_attempts times it stops being claimed,
        and the reconciler routes it to mark_aborted rather than leaving it stuck.
        Scoped to the pre-'running' states, because a battle that reached 'running'
        owes its fighters a verdict and must never be aborted. Bounded by
        ``limit`` for the same reason as find_expired_battle_ids.
        """
        result = await self.db.execute(
            text(
                """
                SELECT id FROM battles
                WHERE status IN ('challenge_pending', 'accepted', 'reserved', 'queued')
                  AND lease_attempt_count >= :max_attempts
                LIMIT :limit
                """
            ),
            {"max_attempts": max_attempts, "limit": limit},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def find_stranded_judging_battle_ids(
        self, max_attempts: int, limit: int
    ) -> list[str]:
        """Judging battles whose attempt budget is spent AND lease has lapsed.

        The escape-hatch finder. The judging-resume phase claims JUDGING only
        while lease_attempt_count < max_attempts; a battle whose judge panel keeps
        throttling burns that budget without ever completing a panel, and then
        claim_battles_for_reconcile stops claiming it — leaving it stranded in
        'judging' forever, unclaimable, with live reservations pinning both
        fighters. This finds exactly those rows so the reaper can settle them
        honestly (collapse the open replicates to error votes, then finalize to a
        no-quorum verdict that rates nothing — a broken judge must not mint Elo).

        The lease-lapsed guard is what keeps this off a battle a live worker is
        still panelling: a fresh claim holds a future lease_expires_at, so only a
        genuinely abandoned row (NULL or past lease) is picked up. Bounded by
        ``limit`` like the other finders.
        """
        result = await self.db.execute(
            text(
                """
                SELECT id FROM battles
                WHERE status = 'judging'
                  AND lease_attempt_count >= :max_attempts
                  AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
                LIMIT :limit
                """
            ),
            {"max_attempts": max_attempts, "limit": limit},
        )
        return [str(row[0]) for row in result.fetchall()]

    async def claim_stranded_judging(
        self, battle_id: str, lease_token: str, lease_seconds: int, max_attempts: int
    ) -> dict | None:
        """Take a fresh lease on a stranded judging battle so it can be settled.

        finalize demands the claim token AND a live lease, so a stranded battle
        (expired lease, spent budget) cannot be settled until a worker re-leases
        it. This is that re-lease, guarded in SQL to exactly the stranded shape:
        status 'judging', budget already spent (``>= max_attempts``, so it can
        never collide with the normal judging-resume claim, which requires
        ``< max_attempts``), and a lapsed lease. None = another worker re-leased
        it first (its UPDATE moved lease_expires_at into the future, so this row's
        predicate no longer holds), which is a normal race, not an error.

        The attempt count is left as-is: the budget is spent on purpose here, and
        incrementing it past the ceiling would change nothing.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET lease_token = CAST(:lease_token AS UUID),
                    lease_expires_at = NOW() + make_interval(secs => :lease_seconds)
                WHERE id = CAST(:battle_id AS UUID)
                  AND status = 'judging'
                  AND lease_attempt_count >= :max_attempts
                  AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
                RETURNING *
                """
            ),
            {
                "battle_id": str(battle_id),
                "lease_token": str(lease_token),
                "lease_seconds": lease_seconds,
                "max_attempts": max_attempts,
            },
        )
        return self._one_or_none(result.mappings().first())

    # -- submissions --------------------------------------------------------

    async def add_submission(
        self,
        battle_id: str,
        side: Side,
        seq_no: int,
        content: str | None,
        is_final: bool,
        tokens_used: int | None = None,
        truncated: bool = False,
        error: str | None = None,
        enforce_deadline: bool = True,
    ) -> bool:
        """Record a checkpoint. False = this slot is taken, or the battle is closed.

        Five rules, all enforced HERE inside the INSERT rather than by a prior
        read — the router's status/deadline checks are a separate statement under
        READ COMMITTED, so between them and this write a battle can transition to
        judging or its deadline can pass:

        * (battle_id, side, seq_no) as the primary key — one row per slot;
        * one final row per side via the partial unique index. A synthetic
          truncated submission for a silent fighter takes that same slot, so a
          late arrival cannot also claim it;
        * seq_no only ever moves FORWARD, via the NOT EXISTS below;
        * the battle must still be 'running' (and, for fighter submissions, still
          before its wall clock) — a turn against a judging battle is scored on a
          frozen answer, and one past the deadline is a backdated one;
        * NOT EXISTS a prior final row for this side (ANY seq). Without it a
          fighter can post a non-final checkpoint AFTER its own final — a lower
          seq_no than the final slips past the monotonicity guard — and the panel
          reads a mutation to an answer it was told was the last word.

        ``enforce_deadline=False`` is for the reconciler's synthetic final only:
        the silent fighter's truncated submission is inserted precisely BECAUSE
        the deadline has passed, while the battle is still 'running' and about to
        be marked judging. It still requires 'running' and the no-prior-final
        rule; it drops only the ``deadline_at > NOW()`` clause a real fighter must
        satisfy.

        The checks are predicates of the INSERT, not a SELECT then an INSERT: a
        read-then-write would let two concurrent submissions both see a clear
        field and both land. Under real concurrency the primary key still
        backstops the equal case, so the worst a race can do is admit a gap,
        never a duplicate or a rewrite.

        ``>=`` rather than ``>`` so the duplicate case is answered by the same
        predicate as the out-of-order one, and both report False identically.
        """
        deadline_clause = "AND b.deadline_at > NOW()" if enforce_deadline else ""
        result = await self.db.execute(
            text(
                f"""
                INSERT INTO battle_submissions
                    (battle_id, side, seq_no, content, tokens_used,
                     is_final, truncated, error)
                SELECT CAST(:battle_id AS UUID), CAST(:side AS VARCHAR(1)),
                       CAST(:seq_no AS INT), :content,
                       :tokens_used, :is_final, :truncated, :error
                WHERE NOT EXISTS (
                    SELECT 1 FROM battle_submissions s
                     WHERE s.battle_id = CAST(:battle_id AS UUID)
                       AND s.side = CAST(:side AS VARCHAR(1))
                       AND s.seq_no >= CAST(:seq_no AS INT)
                )
                  AND NOT EXISTS (
                    SELECT 1 FROM battle_submissions s
                     WHERE s.battle_id = CAST(:battle_id AS UUID)
                       AND s.side = CAST(:side AS VARCHAR(1))
                       AND s.is_final
                )
                  AND EXISTS (
                    SELECT 1 FROM battles b
                     WHERE b.id = CAST(:battle_id AS UUID)
                       AND b.status = 'running'
                       {deadline_clause}
                )
                ON CONFLICT DO NOTHING
                RETURNING seq_no
                """
            ),
            {
                "battle_id": str(battle_id),
                "side": side.value,
                "seq_no": seq_no,
                "content": content,
                "tokens_used": tokens_used,
                "is_final": is_final,
                "truncated": truncated,
                "error": error,
            },
        )
        return result.first() is not None

    async def list_submissions(self, battle_id: str) -> list[dict]:
        """Every checkpoint of a battle, oldest first."""
        result = await self.db.execute(
            text(
                """
                SELECT * FROM battle_submissions
                WHERE battle_id = CAST(:battle_id AS UUID)
                ORDER BY side, seq_no
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return [dict(row) for row in result.mappings()]

    # -- judging ------------------------------------------------------------

    async def create_judge_run(
        self,
        battle_id: str,
        judge_kind: str,
        judge_ref: str,
        replicate_seed: str,
        presented_order: str,
    ) -> str | None:
        """Reserve one raw judge-run slot. None = the slot already exists.

        The key includes presented_order because a replicate is two runs (ab
        and ba) by the same judge — without it the second half of every pair
        would collide and the one available model could never cast more than
        a single run.
        """
        result = await self.db.execute(
            text(
                """
                INSERT INTO battle_judge_runs
                    (battle_id, judge_kind, judge_ref, replicate_seed,
                     presented_order, status)
                VALUES
                    (CAST(:battle_id AS UUID), :judge_kind, :judge_ref,
                     :replicate_seed, :presented_order, 'pending')
                ON CONFLICT ON CONSTRAINT battle_judge_raw_run_once DO NOTHING
                RETURNING id
                """
            ),
            {
                "battle_id": str(battle_id),
                "judge_kind": judge_kind,
                "judge_ref": judge_ref,
                "replicate_seed": replicate_seed,
                "presented_order": presented_order,
            },
        )
        row = result.first()
        return str(row[0]) if row else None

    async def claim_judge_run(
        self,
        run_id: str,
        lease_token: str,
        lease_seconds: int,
        max_attempts: int = 4,
    ) -> dict | None:
        """Claim a judge run under a fresh lease. None = someone else has it.

        Claimable when pending/failed, OR when a previous holder's lease has
        lapsed — a worker that died mid-call leaves the row 'running' forever,
        and refusing to reclaim it would strand the replicate permanently. This
        is what idx_battle_judge_runs_lease exists to serve, and it mirrors
        claim_battles_for_reconcile rather than diverging from it.

        A live lease is never stolen: the lease must outlast the judge's hard
        HTTP timeout, so a call still in flight keeps its row. Rotating the
        token on reclaim is what makes the dead worker's late write bounce.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battle_judge_runs
                SET status = 'running',
                    lease_token = CAST(:lease_token AS UUID),
                    lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                    attempt_count = attempt_count + 1
                WHERE id = CAST(:run_id AS UUID)
                  AND attempt_count < :max_attempts
                  AND (
                      status IN ('pending', 'failed')
                      OR (status = 'running' AND lease_expires_at <= NOW())
                  )
                  AND (lease_expires_at IS NULL OR lease_expires_at <= NOW())
                RETURNING *
                """
            ),
            {
                "run_id": str(run_id),
                "lease_token": str(lease_token),
                "lease_seconds": lease_seconds,
                "max_attempts": max_attempts,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def complete_judge_run(
        self,
        run_id: str,
        lease_token: str,
        vote: str,
        confidence: float | None = None,
        reasoning: str | None = None,
        scores: dict[str, Any] | None = None,
    ) -> dict | None:
        """Write a raw run's verdict. None = the writer no longer owns the row.

        Demands the token AND a live lease. The token alone is not enough: a
        worker whose lease lapsed still holds its original token, so checking
        only the token would let it publish an answer for a replicate someone
        else has since reclaimed — two verdicts for one slot, corrupting the
        quorum arithmetic.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battle_judge_runs
                SET status = 'completed',
                    vote = :vote,
                    confidence = :confidence,
                    reasoning = :reasoning,
                    scores = CAST(:scores AS JSONB),
                    completed_at = NOW(),
                    lease_token = NULL,
                    lease_expires_at = NULL
                WHERE id = CAST(:run_id AS UUID)
                  AND lease_token = CAST(:lease_token AS UUID)
                  AND lease_expires_at > NOW()
                  AND status = 'running'
                RETURNING *
                """
            ),
            {
                "run_id": str(run_id),
                "lease_token": str(lease_token),
                "vote": vote,
                "confidence": confidence,
                "reasoning": reasoning,
                "scores": json.dumps(scores, default=str) if scores else None,
            },
        )
        return self._one_or_none(result.mappings().first())

    async def list_judge_runs(self, battle_id: str) -> list[dict]:
        """Every raw run of a battle — both halves of every replicate pair."""
        result = await self.db.execute(
            text(
                """
                SELECT * FROM battle_judge_runs
                WHERE battle_id = CAST(:battle_id AS UUID)
                ORDER BY replicate_seed, presented_order
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return [dict(row) for row in result.mappings()]

    async def upsert_judgement(
        self,
        battle_id: str,
        judge_kind: str,
        judge_ref: str,
        replicate_seed: str,
        vote: str,
        confidence: float | None = None,
        reasoning: str | None = None,
        scores: dict[str, Any] | None = None,
        position_sensitive: bool = False,
    ) -> str | None:
        """Store one COLLAPSED vote. None = this replicate already voted.

        The unique key without presented_order is what caps three paired
        replicates at three collapsed votes: the two halves of a pair can
        never be counted as two votes, so the quorum cannot be inflated.

        Human votes (phase 2) reuse this with judge_kind='human' and
        judge_ref=user_id, which gives one vote per user per battle for free —
        a second attempt returns None, and the API answers 409.
        """
        result = await self.db.execute(
            text(
                """
                INSERT INTO battle_judgements
                    (battle_id, judge_kind, judge_ref, replicate_seed, vote,
                     confidence, reasoning, scores, position_sensitive)
                VALUES
                    (CAST(:battle_id AS UUID), :judge_kind, :judge_ref,
                     :replicate_seed, :vote, :confidence, :reasoning,
                     CAST(:scores AS JSONB), :position_sensitive)
                ON CONFLICT ON CONSTRAINT battle_judge_once DO NOTHING
                RETURNING id
                """
            ),
            {
                "battle_id": str(battle_id),
                "judge_kind": judge_kind,
                "judge_ref": judge_ref,
                "replicate_seed": replicate_seed,
                "vote": vote,
                "confidence": confidence,
                "reasoning": reasoning,
                "scores": json.dumps(scores, default=str) if scores else None,
                "position_sensitive": position_sensitive,
            },
        )
        row = result.first()
        return str(row[0]) if row else None

    # -- rating -------------------------------------------------------------

    async def lock_fighter_ratings(self, battle_id: str) -> dict | None:
        """Lock both fighters and read the ratings a verdict will move. No commit.

        FOR UPDATE, because the ratings read here are the ones written back: an
        unlocked read-modify-write loses an update when one agent settles two
        battles at once — both read 1200, both write 1200+delta, and one delta
        vanishes. The battle-row CAS in :meth:`finalize` cannot prevent that; it
        serialises the finalizers of ONE battle, whereas this serialises the
        writers of one AGENT across different battles.

        ``ORDER BY id`` is a deadlock rule, not cosmetics: two battles sharing
        the same pair of fighters in opposite roles would otherwise lock them in
        opposite orders and deadlock. One consistent order makes that impossible.

        Returns the owner snapshots too, so the caller decides self-play from the
        ownership frozen AT THE START rather than ownership now — an agent sold
        mid-battle must not retroactively change whether the battle rates.
        """
        battle = await self.db.execute(
            text(
                """
                SELECT agent_a_id, agent_b_id,
                       agent_a_owner_snapshot, agent_b_owner_snapshot
                  FROM battles WHERE id = CAST(:battle_id AS UUID)
                """
            ),
            {"battle_id": str(battle_id)},
        )
        row = self._one_or_none(battle.mappings().first())
        if not row or not row["agent_b_id"]:
            return None

        ratings = await self.db.execute(
            text(
                """
                SELECT id, battle_elo
                  FROM agents
                 WHERE id IN (CAST(:agent_a_id AS UUID), CAST(:agent_b_id AS UUID))
                 ORDER BY id
                   FOR UPDATE
                """
            ),
            {"agent_a_id": str(row["agent_a_id"]), "agent_b_id": str(row["agent_b_id"])},
        )
        elo_by_agent = {str(r["id"]): r["battle_elo"] for r in ratings.mappings()}
        if len(elo_by_agent) != 2:
            return None

        return {
            **row,
            "elo_a": elo_by_agent[str(row["agent_a_id"])],
            "elo_b": elo_by_agent[str(row["agent_b_id"])],
        }

    async def apply_rating(self, agent_id: str, new_elo: int, outcome: str) -> bool:
        """Write one fighter's post-battle rating and counter. No commit.

        ``outcome`` is 'win' | 'loss' | 'tie' and bumps exactly one counter, in
        the SAME statement as the rating: a rating that moved without a recorded
        battle is unauditable, so the two must never be writable apart.

        The caller already holds this row's lock from
        :meth:`lock_fighter_ratings`, so False here is a genuine anomaly (the
        agent vanished) rather than a lost race.
        """
        result = await self.db.execute(
            text(
                """
                UPDATE agents
                SET battle_elo = :new_elo,
                    battle_wins = battle_wins
                        + CASE WHEN :outcome = 'win' THEN 1 ELSE 0 END,
                    battle_losses = battle_losses
                        + CASE WHEN :outcome = 'loss' THEN 1 ELSE 0 END,
                    battle_ties = battle_ties
                        + CASE WHEN :outcome = 'tie' THEN 1 ELSE 0 END
                WHERE id = CAST(:agent_id AS UUID)
                RETURNING id
                """
            ),
            {"agent_id": str(agent_id), "new_elo": new_elo, "outcome": outcome},
        )
        return result.first() is not None

    async def list_judgements(self, battle_id: str) -> list[dict]:
        """Every collapsed vote of a battle."""
        result = await self.db.execute(
            text(
                """
                SELECT * FROM battle_judgements
                WHERE battle_id = CAST(:battle_id AS UUID)
                ORDER BY judge_kind, replicate_seed
                """
            ),
            {"battle_id": str(battle_id)},
        )
        return [dict(row) for row in result.mappings()]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _one_or_none(row: Row | Any | None) -> dict | None:
        """Normalise a CAS result. None means the caller lost the race."""
        return dict(row) if row else None


# Re-exported for callers branching on a claimed run's state without importing
# the schema module twice.
__all__ = [
    "BattleRepository",
    "BattleStatus",
    "ChallengeDenial",
    "JudgeRunStatus",
    "ReservationConflictError",
]
