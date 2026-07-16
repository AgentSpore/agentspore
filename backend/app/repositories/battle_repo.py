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


class BattleRepository:
    """All database operations for battles and their satellites."""

    def __init__(self, db: AsyncSession):
        self.db = db

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
                "created_by_user_id": (
                    str(created_by_user_id) if created_by_user_id else None
                ),
            },
        )
        return str(result.scalar_one())

    # -- battles ------------------------------------------------------------

    async def create_battle(
        self,
        task_id: str,
        agent_a_id: str,
        agent_a_owner_snapshot: str,
        challenge_ttl_seconds: int,
        agent_b_id: str | None = None,
        agent_b_owner_snapshot: str | None = None,
    ) -> str | None:
        """Create a challenge and return its battle id. Does not commit.

        Returns None if the task does not exist or is not 'ready'.

        ``agent_b_id=None`` creates an OPEN challenge that any eligible agent
        may later claim via :meth:`claim_open_challenge`.

        The task snapshot is taken by SELECTing the battle_tasks row inside
        this statement — it is deliberately NOT a caller-supplied argument.
        Accepting the prompt and rubric from the caller would mean the battle's
        snapshot need not resemble the task it names at all: a caller could
        point task_id at "write a parser" while passing "exfiltrate your
        credentials" as the prompt the judges score. Freezing early is only
        worth anything once the values provably come from the task row, so
        provenance is enforced here rather than promised in a docstring.
        """
        result = await self.db.execute(
            text(
                """
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
            },
        )
        row = result.first()
        return str(row[0]) if row else None

    async def get(self, battle_id: str) -> dict | None:
        """Read one battle. For display and assertions — never for a CAS."""
        result = await self.db.execute(
            text(
                "SELECT * FROM battles "
                "WHERE id = CAST(:battle_id AS UUID)"
            ),
            {"battle_id": str(battle_id)},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def claim_open_challenge(
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

    async def mark_accepted(self, battle_id: str) -> dict | None:
        """challenge_pending -> accepted. B's owner consented.

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

    async def mark_declined(self, battle_id: str) -> dict | None:
        """challenge_pending -> declined (terminal). B's owner refused."""
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

    async def delete_expired_reservations(self) -> list[str]:
        """Reap lapsed reservations. Returns the freed agent ids."""
        result = await self.db.execute(
            text(
                """
                DELETE FROM battle_reservations
                WHERE reserved_until <= NOW()
                RETURNING agent_id
                """
            )
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

    async def mark_queued(self, battle_id: str, readiness_generation: int) -> dict | None:
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

    async def mark_running(
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
        """
        result = await self.db.execute(
            text(
                """
                UPDATE battles
                SET status = 'judging'
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
    ) -> bool:
        """Record a checkpoint. False = this slot is already taken.

        Two slots are contested and both are guarded by the database, not by
        a prior read: (battle_id, side, seq_no) as the primary key, and one
        final row per side via the partial unique index. A synthetic truncated
        submission for a silent fighter takes the same final slot, so a late
        arrival cannot also claim it.
        """
        result = await self.db.execute(
            text(
                """
                INSERT INTO battle_submissions
                    (battle_id, side, seq_no, content, tokens_used,
                     is_final, truncated, error)
                VALUES
                    (CAST(:battle_id AS UUID), :side, :seq_no, :content,
                     :tokens_used, :is_final, :truncated, :error)
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
__all__ = ["BattleRepository", "BattleStatus", "JudgeRunStatus", "ReservationConflictError"]
