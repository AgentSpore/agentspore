"""BattleRunner — reconciliation, judging and settlement (step 9).

**The correctness boundary is the database row, not the scheduler.**

``BattleRunTask`` is ``fail_closed=True``, but that is operational throttling,
not a fence. When the Redis leader lease lapses, ``background.py:129`` logs and
returns — ``run_once()`` at :129 keeps executing. So a former leader and a new
leader can run battle work at the same instant, and no amount of care in the
scheduler changes that. Cancellation cannot help either: it is cooperative and
cannot unsend an HTTP request already on the wire.

Therefore every unit of work here is claimed with a per-row PostgreSQL lease and
a token, and every completing write carries that token
(``WHERE id=:id AND lease_token=:token AND status=...``). A worker that lost its
row discovers this at write time and its result is DISCARDED. ``run_once()`` is a
SHORT reconciler: it claims, does one bounded step, and returns. It must never
own a battle for its ten-minute life.

**What is actually guaranteed, and what is not.**

Enforceable, and enforced here:

* exactly-once battle state and Elo (the CAS in ``finalize`` plus the agent row
  locks);
* unique judge-run slots (the raw-run key includes ``presented_order``);
* bounded retry, and rejection of results from a worker that no longer owns the
  row.

NOT enforceable, and not claimed: strictly-once BILLING of an external LLM call.
The provider offers no idempotency key. If we are killed between "z.ai answered"
and "we wrote the answer down", the reclaiming worker calls again and the account
pays twice. What we guarantee is that the second call's result cannot produce a
second vote, a second verdict, or a second Elo change. Anyone who needs
exactly-once billing needs it from the provider, not from this file.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rating import RatingChange, apply_battle_result
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository, ReservationConflictError
from app.schemas.battles import (
    BattleStatus,
    JudgeRunStatus,
    PresentedOrder,
    Side,
    Vote,
    Winner,
)
from app.services.battle_budget import (
    STOP_REASONS,
    BattleJudgeBudgetService,
    JudgeBreakerOpen,
    JudgeBudgetExhausted,
    breaker_is_open,
    breaker_record_attempt,
    breaker_record_failure,
)
from app.services.battle_judges import (
    INJECTION_STOP_REASON,
    JUDGE_KIND_LLM,
    JUDGE_MODEL,
    JUDGE_SYSTEM_PROMPTS,
    PRESENTED_ORDERS,
    REPLICATE_COUNT,
    CollapsedVote,
    JudgeInjectionSuspected,
    JudgeModel,
    JudgeRunResult,
    JudgeTransportError,
    PanelVerdict,
    build_judge_messages,
    build_judge_payload,
    call_judge_model,
    collapse_pair,
    parse_judge_response,
    replicate_seed,
    resolve_verdict,
    rubric_keys,
    scan_submissions,
    wire_model_name,
)
from app.services.battle_service import BattleService
from app.services.connection_manager import dispatch_existing
from app.services.llm_gate import LLMGate

# Row-lease length for a battle claimed by the reconciler. Long enough to
# outlast one bounded step (six judge calls at 60s each, queued behind a
# 3-slot gate), short enough that a worker that dies does not strand the
# battle for long. Renewed while a step is actually in flight.
BATTLE_LEASE_SECONDS = 300

# Row-lease for ONE judge run. MUST exceed JUDGE_HTTP_TIMEOUT_SECONDS (60) plus
# the gate wait, or a live call's row is reclaimed under it and two workers
# publish for one slot.
JUDGE_RUN_LEASE_SECONDS = 180

# Attempt ceiling for ONE raw judge run, matching claim_judge_run's default. A
# replicate half that keeps failing transport is reclaimed up to this many times;
# once spent, it collapses to a terminal 'error' vote so the panel can conclude
# rather than waiting on a slot that will never answer.
JUDGE_RUN_MAX_ATTEMPTS = 4

# How far past a battle's deadline both fighters stay reserved after it starts.
# Small: the reservation must outlast the whole battle so a fighter cannot be
# double-booked mid-fight, but finalize frees it, so the margin only has to cover
# the reconciler's own settling latency.
RESERVATION_START_MARGIN_SECONDS = 60

# Battles claimed per reconciler pass. A ceiling, not a target: a pass must
# stay short, because the scheduler tick is the only thing bounding it.
RECONCILE_BATCH = 20

# Attempt ceiling for the CHEAP compare-and-set phases (reserved -> queued,
# queued -> running). Deliberately far above claim_battles_for_reconcile's
# default of 4.
#
# That default is sized for work that SPENDS money: a battle whose judge pass
# keeps crashing must stop being retried. These two phases spend nothing — they
# are one guarded UPDATE — and a reserved battle is polled once per tick while
# it legitimately waits for two agents to ack. At a 30s tick, a ceiling of 4
# would abandon a battle after two minutes of ordinary waiting and strand it in
# 'reserved' forever. The real bound on waiting is the readiness lease
# (ready_lease_expires_at), which release_expired_readiness enforces; the ceiling
# here is only a crash-loop guard.
POLL_MAX_ATTEMPTS = 500

# The money phase keeps claim_battles_for_reconcile's strict default: a battle
# whose judge pass keeps crashing must stop being retried.
RUNNING_MAX_ATTEMPTS = 4

# The cheap phases claim WITHOUT holding a lease, and that is deliberate.
#
# BATTLE_LEASE_SECONDS (300) is sized for the money phase: six judge calls
# queued behind a 3-slot gate need the row held that long. Applying it to a poll
# phase is actively wrong — it would make a reserved battle re-checkable only
# once every five minutes, while the readiness lease it is waiting on is 60s. The
# battle would be released for "not acking" without ever having been asked twice.
#
# Zero is safe because these phases do not need a lease for correctness. Decision
# #1: the fence is the per-row compare-and-set, not the lease. arm_readiness,
# admit_to_queue and start_if_still_eligible each re-prove every precondition in
# one guarded statement and none of them reads lease_token. Two workers racing a
# cheap CAS costs one wasted statement; exactly one still wins. The lease exists
# to stop duplicate EXPENSIVE work, and there is none here.
#
# lease_attempt_count still increments, so POLL_MAX_ATTEMPTS remains the
# crash-loop guard.
POLL_LEASE_SECONDS = 0

# The reserved -> queued binding is the ONE cheap phase that IS lease-fenced
# (V67). Binding chooses and cools down a concrete task, and admit_to_queue
# requires the row's lease_token AND a live lease_expires_at, so the reserved
# phase must claim with a REAL, short lease rather than the zero above — long
# enough for the single binding statement (which also takes the global
# task-pool advisory lock), far shorter than BATTLE_LEASE_SECONDS so a crashed
# binder frees the row within seconds. The fence stops two workers from binding
# (and double-cooling a task on) the same battle; the global advisory lock
# inside the statement stops two DIFFERENT battles from selecting the same task.
TASK_BIND_LEASE_SECONDS = 15

# The synthetic final submission a silent fighter receives at the deadline.
# A battle that reached 'running' is owed a verdict — both fighters were provably
# eligible at the shared start — so silence becomes an empty truncated answer to
# be judged, never a retroactive abort.
SILENT_FIGHTER_SEQ_NO = 9_999
SILENT_FIGHTER_ERROR = "no submission before deadline"


def _outcome_for(side: Side, winner: Winner | None) -> str:
    """Map a verdict onto the counter one fighter's row increments."""
    if winner is Winner.TIE:
        return "tie"
    if winner is None:
        return "tie"  # unreachable: callers skip counters entirely when unrated
    won = (winner is Winner.A and side is Side.A) or (winner is Winner.B and side is Side.B)
    return "win" if won else "loss"


class BattleRunner:
    """Owns the transaction boundary for every step-9 transition.

    One instance per unit of work. The session is passed in (router/task owns
    it), and this class decides where the commits fall — the repository never
    commits, so the atomicity claims below are made here or nowhere.
    """

    def __init__(
        self, db: AsyncSession, gate: LLMGate, http: httpx.AsyncClient | None = None
    ) -> None:
        self.db = db
        self.repo = BattleRepository(db)
        self.gate = gate
        self.http = http

    # -- settlement ---------------------------------------------------------

    async def settle_battle(
        self,
        battle_id: str,
        lease_token: str,
        override_verdict: PanelVerdict | None = None,
    ) -> RatingChange | None:
        """judging -> completed, with the verdict and both ratings, atomically.

        THE invariant of step 9 lives in this method: winner, Elo snapshots,
        both agents' ratings, counters, reservation release and the 'completed'
        status all commit in ONE transaction, or none of them do.

        Ordering is load-bearing:

        1. Lock both agents and read the ratings the verdict will move. Taking
           the locks first means a concurrent settlement of a SHARED fighter
           waits here rather than interleaving a read-modify-write.
        2. Compute the rating purely, from those locked values.
        3. ``finalize()`` — the CAS. This is the gate. If it returns None a
           concurrent finalizer already completed this battle, so we roll back:
           the agent locks release having written nothing, and the rating we
           computed honestly is discarded rather than applied a second time.
        4. Only then write the ratings.

        ``override_verdict`` FORCES the outcome instead of reading the persisted
        judge votes. It exists for the injection-disqualification path (F3): a
        battle whose sole injecting fighter is disqualified has no honest panel
        verdict — the panel never ran — so the outcome is dictated (winner = the
        clean opponent) rather than derived. Every other clause (owner-lock,
        same-owner gate, rated gate, finalize CAS, rating write, notify) is
        identical, so a forced verdict still rates only when the battle was
        rated-eligible and the owners differ. NEVER pass an override that names
        the injector as winner — the caller owns that invariant.

        Returns None when this worker lost the race, which is a normal outcome
        and not an error. Returns an unapplied RatingChange when the battle
        legitimately rates nothing (no quorum, or same-owner self-play).
        """
        if override_verdict is not None:
            verdict = override_verdict
        else:
            judgements = await self.repo.list_judgements(battle_id)
            votes = [
                CollapsedVote(
                    replicate_seed=str(j["replicate_seed"]),
                    vote=Vote(j["vote"]),
                    confidence=j["confidence"],
                    position_sensitive=bool(j["position_sensitive"]),
                )
                for j in judgements
            ]
            verdict = resolve_verdict(votes)

        fighters = await self.repo.lock_fighter_ratings(battle_id)
        if fighters is None:
            logger.warning("battle {} cannot settle: fighters unreadable", battle_id)
            await self.db.rollback()
            return None

        # Self-play is decided from the FROZEN owner snapshots, not from
        # ownership now: an agent sold mid-battle must not retroactively change
        # whether the battle rated.
        same_owner = (
            fighters["agent_a_owner_snapshot"] is not None
            and fighters["agent_a_owner_snapshot"] == fighters["agent_b_owner_snapshot"]
        )

        winner = self._verdict_to_winner(verdict.winner, verdict.is_tie)

        # Rating gate (V68 Track 3). A battle only affects Elo if it reserved a
        # rated slot at acceptance (rated_eligible TRUE — the anti-Sybil gate in
        # BattleService.accept passed: distinct verified owners, both old enough,
        # within the daily/concurrent rated quota), the panel reached quorum, no
        # budget/breaker stop cut judging short, and the frozen owners differ.
        # The V68 battle_rated_requires_eligibility CHECK enforces the first
        # clause structurally: is_rated=TRUE is illegal unless rated_eligible=TRUE.
        judging_stopped = fighters["judging_stop_reason"] is not None
        # Quarantine backstop (V70). A quarantined task is a user submission that
        # no moderator has approved, so its AUTHOR knows the answer — rating a
        # battle fought on one would hand out real Elo for prepared work. The
        # primary defence is the pool split in admit_to_queue, which refuses to
        # bind a quarantined task to a rated-eligible battle at all; this clause
        # is the second line, and it must never be the one that fires. If it
        # does, the battle still completes and is still shown — it simply does
        # not rate, which is the same treatment every other ineligibility gets.
        task_in_quarantine = bool(fighters["task_in_quarantine"])
        should_rate = (
            fighters["rated_eligible"] is True
            and winner is not None
            and not same_owner
            and not judging_stopped
            and not task_in_quarantine
        )
        change = apply_battle_result(
            fighters["elo_a"],
            fighters["elo_b"],
            winner,
            rated=should_rate,
        )

        reason = verdict.reason
        if same_owner:
            # Recorded, not hidden: the battle happened and is worth showing;
            # it simply does not rate, or one owner farms rating against
            # themselves for the price of inference.
            reason = f"{reason}; same-owner self-play — rating unchanged"

        completed = await self.repo.finalize(
            battle_id=battle_id,
            lease_token=lease_token,
            winner=winner.value if winner else None,
            verdict_reason=reason,
            is_rated=should_rate,
            judging_stop_reason=fighters["judging_stop_reason"],
            # Only stamped when the backstop actually bit, and finalize COALESCEs
            # it so an earlier, more specific acceptance-time reason survives.
            rated_ineligibility_reason=(
                BattleService.TASK_IN_QUARANTINE_REASON if task_in_quarantine else None
            ),
            elo_a_before=change.a_before,
            elo_b_before=change.b_before,
            elo_a_after=change.a_after,
            elo_b_after=change.b_after,
        )
        if completed is None:
            # Lost the race, or the lease lapsed. Someone else's verdict is
            # authoritative now. Roll back so the agent locks release clean.
            await self.db.rollback()
            logger.info("battle {} already finalized by another worker", battle_id)
            return None

        if change.applied:
            await self.repo.apply_rating(
                str(fighters["agent_a_id"]), change.a_after, _outcome_for(Side.A, winner)
            )
            await self.repo.apply_rating(
                str(fighters["agent_b_id"]), change.b_after, _outcome_for(Side.B, winner)
            )

        await self.repo.release_reservations(battle_id)
        await self.db.commit()

        logger.info(
            "battle {} completed: winner={} elo {}->{} / {}->{} (applied={})",
            battle_id,
            winner.value if winner else "none",
            change.a_before,
            change.a_after,
            change.b_before,
            change.b_after,
            change.applied,
        )

        # Best-effort: tell both owners how it ended. The battle is already
        # completed and durable above; this must not be able to undo it.
        await _notify_battle_owners(
            self.db,
            str(battle_id),
            [
                (
                    str(completed["agent_a_id"]),
                    "battle_result",
                    _battle_result_title(str(battle_id), Side.A, completed["winner"]),
                ),
                (
                    str(completed["agent_b_id"]),
                    "battle_result",
                    _battle_result_title(str(battle_id), Side.B, completed["winner"]),
                ),
            ],
        )
        return change

    @staticmethod
    def _verdict_to_winner(side: Side | None, is_tie: bool) -> Winner | None:
        """Map a panel verdict onto the battle's winner column.

        The two None-shaped outcomes are deliberately NOT merged: ``is_tie``
        means the replicates reached a verdict of "draw" (which rates), while
        no-quorum means they reached no verdict at all (which does not). Folding
        one into the other would mint tie-Elo out of a panel that never spoke.
        """
        if side is Side.A:
            return Winner.A
        if side is Side.B:
            return Winner.B
        if is_tie:
            return Winner.TIE
        return None

    # -- admission ----------------------------------------------------------

    async def arm_accepted(self, battle: dict) -> bool:
        """accepted -> reserved, then PUSH the ready-checks. False = not now.

        Why the reconciler owns this and not the accept route. Accepting is a
        human decision — fact 1 of the four this design keeps apart — and it must
        not be able to fail for a reason that has nothing to do with consent.
        Arming can fail: reserve_both raises when either fighter is already
        reserved in another battle, which is ordinary and temporary. If accept
        armed inline, an owner clicking "accept" while their agent finished
        another battle would get a 409 for a consent that was perfectly valid,
        and the battle would need a second click that the UI never asks for.
        Here it simply waits and the next pass arms it.

        The same split keeps accept off the transport path: dispatch happens
        AFTER the commit, so a slow or dead WebSocket cannot make accept hang or
        roll back. The rows are durable before anything is sent — a dispatch
        failure costs latency, not the event, because the heartbeat drain still
        carries it.
        """
        battle_id = str(battle["id"])
        service = BattleService(self.db)

        try:
            armed = await service.arm_readiness(battle_id)
        except ReservationConflictError:
            # A fighter is busy in another battle. Not an error — try next pass.
            await self.db.rollback()
            return False
        if armed is None:
            await self.db.rollback()
            return False

        await self.db.commit()

        # Outbox discipline: persisted first, sent second, and via
        # dispatch_existing so the armed ids are the ones that travel. Readiness
        # is bound to those exact ids, so a duplicate row would be un-ackable.
        #
        # A transport failure is swallowed DELIBERATELY, and this is the one
        # place that is correct: the arming is already committed, so raising
        # here would report failure for work that durably succeeded — the caller
        # would log "arm failed" and not count a battle that is, in fact,
        # reserved. The events are durable rows; the heartbeat drain delivers
        # them regardless, and an agent that acks via heartbeat inside the lease
        # is perfectly ready. Latency is the only cost.
        try:
            await service.dispatch_ready_checks(armed)
        except Exception as exc:
            logger.warning(
                "battle {} armed, but ready-check dispatch failed ({}); "
                "heartbeat drain will carry them",
                battle_id,
                exc,
            )
        logger.info("battle {} reserved: ready-checks dispatched", battle_id)
        return True

    async def admit_reserved(self, battle: dict, lease_token: str) -> bool:
        """reserved -> queued (and bind a task) once both ACKs are in (V67).

        Delegates the decision to BattleService.try_queue -> admit_to_queue,
        which re-proves consent, eligibility, ownership, both live reservations,
        both exact current-generation ACK ids AND the processing lease in ONE
        statement, then binds a random fresh task matching the battle's filter.
        Nothing is re-checked here, because a second opinion computed in Python
        would be a different, weaker predicate evaluated at a different instant.

        ``lease_token`` is the reconciler claim token this battle was claimed
        with (the reserved phase now claims WITH a real lease — see
        TASK_BIND_LEASE_SECONDS), so only this worker may bind it.

        Three outcomes when try_queue returns None, distinguished in SQL, never
        inferred:

        * both sides ACKed but no fresh task matches the filter -> abort the
          battle honestly (pool exhausted), release both reservations, notify;
        * the readiness lease lapsed -> release back to 'accepted' for another
          arm, or abort once the re-arm budget is spent (a never-ACK opponent
          must not pin the challenger for the whole challenge TTL);
        * still simply waiting for an ACK -> leave reserved and retry next pass.

        False is the ordinary case, not an error.
        """
        battle_id = str(battle["id"])
        generation = battle["readiness_generation"]
        service = BattleService(self.db)

        queued = await service.try_queue(battle_id, generation, lease_token)
        if queued is not None:
            await self.db.commit()
            logger.info("battle {} queued: both fighters acked, task bound", battle_id)
            return True

        # try_queue said no. Before treating this as "not ready yet", check the
        # one case where readiness IS proven but binding still cannot happen: the
        # requested filter has fewer than the minimum fresh tasks. abort_pool_
        # exhausted re-proves the full ACK/lease predicate set in its own CAS, so
        # a battle merely still waiting for an ACK does not match and falls
        # through to the readiness path below.
        exhausted = await service.abort_pool_exhausted(battle_id, generation, lease_token)
        if exhausted is not None:
            await self.db.commit()
            logger.info(
                "battle {} aborted: task pool exhausted for its filter, "
                "reservations released",
                battle_id,
            )
            await self._notify_aborted(battle_id, exhausted)
            return False

        # Not exhausted and not queued. If the lease has lapsed, either release
        # it back to 'accepted' for another arm, or — once the re-arm budget is
        # spent — abort it so a never-ACK opponent cannot pin the challenger for
        # the whole challenge TTL. Both happen in the SAME transaction as the
        # state change; the abort notification fires after the commit.
        outcome = await service.expire_or_abort_readiness(battle_id)
        await self.db.commit()
        if outcome is None:
            return False
        if outcome["outcome"] == "aborted":
            aborted = outcome["battle"]
            logger.info(
                "battle {} readiness cap reached ({} silent): aborted, reservations released",
                battle_id,
                outcome["silent_sides"] or "none",
            )
            await self._notify_aborted(battle_id, aborted)
        else:
            logger.info("battle {} readiness lapsed: reservations released", battle_id)
        return False

    async def _notify_aborted(self, battle_id: str, aborted: dict) -> None:
        """Tell both owners a pre-start battle was aborted. After the commit."""
        title = f"Бой прерван (бой {battle_id})"
        recipients = [(str(aborted["agent_a_id"]), "battle_aborted", title)]
        if aborted["agent_b_id"]:
            recipients.append((str(aborted["agent_b_id"]), "battle_aborted", title))
        await _notify_battle_owners(self.db, battle_id, recipients)

    async def start_queued(self, battle: dict, lease_token: str) -> bool:
        """queued -> running, with BOTH battle_turn rows, in one transaction.

        This is the moment the money starts being spent, and the two failure
        windows it closes are the reason the outbox exists:

        * commit 'running' first, crash before the events -> a battle runs with
          no task and both fighters are scored on silence they never saw;
        * send the events first, crash before 'running' -> fighters burn their
          owners' budget on a battle that never started.

        So the rows are INSERTed in the transaction that flips the status, and
        transport happens after the commit. dispatch_existing, never
        deliver_event: the rows are already persisted, and deliver_event would
        insert a SECOND row for a durable type — a fighter acking the duplicate
        would ack an event no battle is bound to.

        The turn TTL is the battle's own time limit, so the event expires exactly
        when deadline_at does, by construction: both derive from NOW() and
        time_limit_seconds_snapshot inside this one statement. Never the 32400s
        default — a turn that stays live for nine hours outlives its battle.
        """
        battle_id = str(battle["id"])
        started = await self.repo.start_if_still_eligible(
            battle_id=battle_id,
            lease_token=lease_token,
            lease_seconds=BATTLE_LEASE_SECONDS,
        )
        if started is None:
            # Lost the CAS, or a fighter stopped being eligible between queueing
            # and starting — an owner change or a reaped reservation. Not an
            # error: the battle simply does not start.
            await self.db.rollback()
            return False

        # The snapshots are nullable since V67 (bound only at reserved -> queued),
        # but a battle that reached 'queued' is bound by the
        # battle_task_bound_from_queue CHECK, so a NULL here is a corruption, not
        # a normal state. Fail before dispatching rather than ship a battle_turn
        # carrying a null prompt. The values themselves are NEVER logged — a
        # prompt or rubric in a log is exactly the leak this track exists to
        # prevent; the error names only WHICH field was null.
        missing = [
            name
            for name in ("task_prompt_snapshot", "task_rubric_snapshot",
                         "time_limit_seconds_snapshot")
            if started[name] is None
        ]
        if missing:
            await self.db.rollback()
            raise RuntimeError(
                f"battle {battle_id} reached 'queued' with unbound task "
                f"snapshot(s): {', '.join(missing)}"
            )

        # Extend both reservations through the deadline, in THIS transaction. The
        # 90s readiness reservation is far shorter than a battle that can run for
        # an hour; without this the hold lapses mid-fight and a second battle can
        # double-book the fighter. Frozen alongside the start so the hold and the
        # deadline are set from one snapshot.
        await self.repo.extend_reservations(battle_id, RESERVATION_START_MARGIN_SECONDS)

        events = AgentEventRepository(self.db)
        ttl = int(started["time_limit_seconds_snapshot"])
        dispatch: list[tuple[str, str]] = []
        for side, agent_key in (("a", "agent_a_id"), ("b", "agent_b_id")):
            agent_id = str(started[agent_key])
            event_id = await events.create(
                target_agent_id=agent_id,
                event_type="battle_turn",
                payload={
                    "type": "battle_turn",
                    "battle_id": battle_id,
                    "side": side,
                    "prompt": started["task_prompt_snapshot"],
                    "rubric": started["task_rubric_snapshot"],
                    "deadline_at": str(started["deadline_at"]),
                    "time_limit_seconds": ttl,
                },
                ttl_seconds=ttl,
            )
            dispatch.append((agent_id, event_id))

        await self.db.commit()

        # After the commit, and best-effort by design: the rows are durable, so
        # a transport failure costs latency, not the task — the heartbeat drain
        # still carries it. Swallowed for the same reason as in arm_accepted:
        # the battle is ALREADY running and its deadline is already ticking, so
        # raising here would report a failure for a start that really happened
        # and leave the caller's count disagreeing with the database.
        for agent_id, event_id in dispatch:
            try:
                await dispatch_existing(agent_id, event_id)
            except Exception as exc:
                logger.warning(
                    "battle {} started, but turn dispatch to {} failed ({}); "
                    "heartbeat drain will carry it",
                    battle_id,
                    agent_id,
                    exc,
                )

        logger.info("battle {} running until {}", battle_id, started["deadline_at"])
        return True

    # -- deadline reconciliation --------------------------------------------

    async def close_deadline(self, battle_id: str, lease_token: str) -> bool:
        """Synthesize silence at the deadline, then running -> judging.

        The synthesis is GATED on the wall clock, and that gate is the whole
        point of this method. It fires only when the battle is actually finished:

        * the deadline has passed — a silent side gets a truncated synthetic
          final so the battle is judged on what it had, never aborted; or
        * both sides already submitted a real final — an early, legitimate finish.

        A running battle that is still BEFORE its deadline with fewer than two
        real finals is NOT finished. Synthesizing finals for it would satisfy
        ``mark_judging``'s "both sides final" branch and close a live battle the
        instant its 300s row-lease lapsed — minutes of real answering time thrown
        away. So this releases the claim and leaves it running: the release also
        undoes the poll's attempt increment, because waiting out a deadline is not
        a money-phase processing attempt (see release_reconcile_claim).

        ``add_submission`` returning False means the fighter's real answer beat us
        to the slot by a hair — the partial unique index arbitrated it and their
        answer wins. The synthetic insert passes ``enforce_deadline=False``: it is
        added precisely because the deadline has passed, while the battle is still
        'running'.
        """
        battle = await self.repo.get(battle_id)
        if battle is None or battle["status"] != BattleStatus.RUNNING.value:
            await self.db.rollback()
            return False

        finalised_sides = {
            str(s["side"]) for s in await self.repo.list_submissions(battle_id) if s["is_final"]
        }
        both_final = finalised_sides >= {Side.A.value, Side.B.value}
        deadline = battle["deadline_at"]
        deadline_passed = deadline is not None and deadline <= datetime.now(UTC)

        if not both_final and not deadline_passed:
            # Still running, real work in flight, deadline in the future. Do not
            # synthesize and do not transition — just let go of the claim.
            await self.repo.release_reconcile_claim(battle_id, lease_token)
            await self.db.commit()
            return False

        for side in (Side.A, Side.B):
            if side.value not in finalised_sides:
                await self.repo.add_submission(
                    battle_id=battle_id,
                    side=side,
                    seq_no=SILENT_FIGHTER_SEQ_NO,
                    content=None,
                    is_final=True,
                    truncated=True,
                    error=SILENT_FIGHTER_ERROR,
                    enforce_deadline=False,
                )

        judging = await self.repo.mark_judging(battle_id, lease_token)
        if judging is None:
            await self.db.rollback()
            return False

        await self.db.commit()
        return True

    # -- judging ------------------------------------------------------------

    def _resolve_judge_roster(self, base_url: str, api_key: str) -> list[JudgeModel]:
        """Build the per-replicate model roster from config (Track 2 diversity).

        The primary entry is always JUDGE_MODEL with the credentials this pass was
        given (base_url/api_key, resolved upstream). Any ADDITIONAL id in
        ``settings.battle_judge_models`` is added only if OpenRouterService
        resolves a usable key for it, so the roster reflects what is actually
        reachable and never a hardcoded list. In practice only the primary
        resolves (RU-ASN geo-block), so this returns ``[primary]`` and the panel
        runs prompt-diversity only — the honest, recorded degraded mode.

        ``wire_model`` is the id STRIPPED of its provider prefix. It used to be
        kept equal to ``model_id`` on the claim that this preserved the exact,
        live-verified request; that claim was false. The provider rejects the
        prefixed form with ``400 {"code":"1211","message":"Unknown Model"}`` on
        this very model, verified live — the prefixed request was never the one
        that worked. It went unnoticed because the out-of-range ``seed`` on the
        same request returned 400 first and masked it.

        The strip is NOT deferred to "when a second provider is enabled": the
        prefix is the platform's own convention, so every id in the roster
        carries it and every id needs the same treatment. Should some provider
        ever want a name we cannot derive by stripping, that is a per-provider
        mapping to add THEN — it does not justify shipping an id no provider
        accepts now.
        """
        primary = JudgeModel(
            model_id=JUDGE_MODEL,
            provider=JUDGE_MODEL.split("/", 1)[0],
            base_url=base_url,
            api_key=api_key,
            wire_model=wire_model_name(JUDGE_MODEL),
        )
        settings = get_settings()
        extra_ids = [m for m in settings.battle_judge_models if m != JUDGE_MODEL]
        if not extra_ids:
            return [primary]

        # Local import: OpenRouterService pulls in the wider service graph that
        # imports core.background at its top — the same cycle BattleRunTask
        # documents. Cached after first use.
        from app.services.openrouter_service import OpenRouterService  # noqa: PLC0415

        svc = OpenRouterService()
        roster = [primary]
        for mid in extra_ids:
            creds = svc.resolve_provider(mid)
            if creds is None:
                continue
            roster.append(
                JudgeModel(
                    model_id=mid,
                    provider=mid.split("/", 1)[0],
                    base_url=creds["base_url"],
                    api_key=creds["api_key"],
                    wire_model=wire_model_name(mid),
                )
            )
        return roster

    async def run_judge_panel(
        self,
        battle_id: str,
        api_key: str,
        base_url: str,
        lease_token: str,
        budget: BattleJudgeBudgetService | None = None,
    ) -> list[CollapsedVote]:
        """Run three paired replicates and persist the collapsed votes.

        Each raw run is its own claimed row, so a restart resumes rather than
        restarting: slots already completed are skipped by the unique key, and
        only the missing halves are re-called. This is what makes
        "reconciliation after restart produces the same state" true.

        Two things this method does NOT do naively:

        * It RENEWS the battle lease after every completed half. Six judge calls
          at up to four attempts of 60s each can outrun the 300s battle lease, and
          a lapsed lease makes finalize's CAS reject the verdict this panel
          computed honestly — silently discarding a real result. If a renewal
          fails we no longer own the battle, so the panel aborts and lets the new
          owner run it.

        * It persists a replicate's collapsed vote ONLY when both halves are
          terminal — completed with a real vote, or with their own attempt budget
          spent. A half that hit a transient transport throttle stays 'running'
          with attempts left and is left for a later pass, because upsert_judgement
          is ON CONFLICT DO NOTHING: collapsing a transient error to a frozen
          'error' vote now would block the correct re-run from ever recording, and
          settle would then complete the battle with no quorum. Only an exhausted
          budget produces the terminal error vote that lets the panel conclude.

        Judging is idempotent at the SLOT level, not the call level — see the
        module docstring on billing.
        """
        battle = await self.repo.get(battle_id)
        if battle is None:
            return []

        submissions = await self.repo.list_submissions(battle_id)
        final_by_side = {str(s["side"]): s["content"] for s in submissions if s["is_final"]}
        rubric = battle["task_rubric_snapshot"] or []
        allowed = rubric_keys(rubric)

        # NOTES — what this defense IS and IS NOT (honest scope; do not overclaim).
        #
        # The pre-panel injection scan + per-side disqualification (F3) and the
        # three fixed paraphrases are DEFENSE-IN-DEPTH: they stop naive/obvious
        # injections before spend and punish an attributable injector. They are
        # NOT independent judges and NOT, on their own, a sufficient gate for rated
        # Elo against a determined adversary:
        #   * only ONE judge model is reachable (RU-ASN geo-block leaves z.ai), so
        #     model diversity is DORMANT — the roster degrades to single-model;
        #   * three fixed paraphrases of ONE public prompt on ONE profileable model
        #     are correlated samples, not independent verdicts;
        #   * the detector is a LEXICAL, English-biased filter, bypassable by
        #     construction (encoded decode-and-follow payloads, non-English or
        #     Unicode-confusable injections, semantic rubric-gaming with no trigger
        #     words). See battle_judges._INJECTION_PATTERNS.
        # A robust rated gate needs a SECOND reachable model or a trained/semantic
        # classifier. Until then, treat automated injection defense as one layer,
        # with the judge-as-untrusted-data instruction and quorum behind it.
        #
        # Injection scan runs BEFORE any paid call. Raising here lets
        # _judge_and_settle attribute and settle (disqualify one injector, or UNRATE
        # if both) instead of spending budget. Deterministic on the stored finals,
        # so every judging pass re-detects and the settle is idempotent. Only the
        # matched pattern classes and the offending side are logged — never text.
        findings = scan_submissions(final_by_side)
        if findings:
            logger.warning(
                "battle {} quarantined: injection shapes in submission(s) [{}]",
                battle_id,
                "; ".join(f"{f.side.value}:{','.join(f.patterns)}" for f in findings),
            )
            raise JudgeInjectionSuspected(findings)

        # Resolve the per-replicate model roster from config (Track 2 diversity).
        roster = self._resolve_judge_roster(base_url, api_key)
        if len(roster) == 1:
            logger.info(
                "battle {} judge panel single-model ({}): prompt-diversity only",
                battle_id,
                roster[0].model_id,
            )

        collapsed: list[CollapsedVote] = []
        for replicate_no in range(REPLICATE_COUNT):
            seed = replicate_seed(battle_id, replicate_no)
            halves: list[JudgeRunResult] = []
            # A different model (where >1 reachable) AND a different system-prompt
            # paraphrase per replicate, so no single injected string can steer all
            # three identically.
            model = roster[replicate_no % len(roster)]
            system_prompt = JUDGE_SYSTEM_PROMPTS[replicate_no % len(JUDGE_SYSTEM_PROMPTS)]

            for order in PRESENTED_ORDERS:
                halves.append(
                    await self._run_one_half(
                        battle_id=battle_id,
                        battle=battle,
                        seed=seed,
                        order=order,
                        rubric=rubric,
                        allowed=allowed,
                        submission_a=final_by_side.get(Side.A.value),
                        submission_b=final_by_side.get(Side.B.value),
                        model=model,
                        system_prompt=system_prompt,
                        battle_lease_token=lease_token,
                        budget=budget,
                    )
                )
                renewed = await self.repo.renew_battle_lease(
                    battle_id, lease_token, BATTLE_LEASE_SECONDS
                )
                await self.db.commit()
                if not renewed:
                    logger.warning(
                        "battle {} judge panel aborted: lease lost mid-panel", battle_id
                    )
                    return collapsed

            runs_by_order = {
                str(r["presented_order"]): r
                for r in await self.repo.list_judge_runs(battle_id)
                if str(r["replicate_seed"]) == seed
            }
            if not all(
                self._half_is_terminal(runs_by_order.get(order.value))
                for order in PRESENTED_ORDERS
            ):
                # A half is still reclaimable — leave this replicate for a later
                # pass rather than freezing a transient failure into an error vote.
                continue

            vote = collapse_pair(halves[0], halves[1], seed)
            # One collapsed vote per replicate — the unique key without
            # presented_order makes a second attempt a no-op rather than a
            # second vote. judge_ref is THIS replicate's model, so a diversified
            # panel records which model cast each vote (and a homogeneous set is
            # how single-model runs stay auditable).
            await self.repo.upsert_judgement(
                battle_id=battle_id,
                judge_kind=JUDGE_KIND_LLM,
                judge_ref=model.model_id,
                replicate_seed=seed,
                vote=vote.vote.value,
                confidence=vote.confidence,
                reasoning=vote.reasoning,
                scores=vote.scores,
                position_sensitive=vote.position_sensitive,
            )
            await self.db.commit()
            collapsed.append(vote)

        return collapsed

    async def collapse_open_replicates_to_error(self, battle_id: str) -> int:
        """Freeze every not-yet-decided replicate as a terminal 'error' vote.

        The escape-hatch counterpart to run_judge_panel's per-replicate collapse,
        called ONLY when the battle's attempt budget is spent — so a replicate
        with no judgement will never get one, and its silence is now a definitive
        error rather than a transient throttle. upsert_judgement is
        ON CONFLICT DO NOTHING, so a replicate that DID reach a real vote keeps it;
        only the genuinely open ones become 'error'. Error votes leave the quorum
        denominator (resolve_verdict), so the settle that follows resolves to
        no-quorum and rates nothing instead of inventing a side or a tie.

        Does not commit — the caller owns the transaction boundary.
        """
        for replicate_no in range(REPLICATE_COUNT):
            await self.repo.upsert_judgement(
                battle_id=battle_id,
                judge_kind=JUDGE_KIND_LLM,
                judge_ref=JUDGE_MODEL,
                replicate_seed=replicate_seed(battle_id, replicate_no),
                vote=Vote.ERROR.value,
                reasoning="attempt budget exhausted before a verdict",
            )
        return REPLICATE_COUNT

    async def _stamp_and_settle_unrated(
        self, battle_id: str, lease_token: str, reason: str, log_label: str
    ) -> RatingChange | None:
        """Stamp a stop-reason, collapse open replicates to error, settle UNRATED.

        The shared terminal path for a battle that must complete without a rated
        verdict: the panel can never reach quorum, so stamp the public-safe
        ``judging_stop_reason``, freeze every still-open replicate to a terminal
        error (which leaves the quorum denominator -> no-quorum), and settle.
        Returns None when this worker no longer owns the battle (lost the lease),
        in which case another owner resolves it.
        """
        stamped = await self.repo.set_judging_stop_reason(battle_id, reason, lease_token)
        if stamped is None:
            await self.db.rollback()
            logger.info("battle {} {} settle skipped: lease lost", battle_id, log_label)
            return None
        await self.collapse_open_replicates_to_error(battle_id)
        await self.db.commit()
        # settle_battle owns its own transaction; it reads the now-committed
        # judging_stop_reason, so should_rate is False and finalize writes
        # is_rated=False + the reason, satisfying battle_is_rated_terminal.
        return await self.settle_battle(battle_id, lease_token)

    async def settle_budget_exhausted(
        self, battle_id: str, lease_token: str, reason: str
    ) -> RatingChange | None:
        """Terminally settle a battle whose judge budget ran out (V68 B).

        The budget for this period is spent, so the panel can never reach quorum;
        settle it UNRATED, no-quorum, honest — rather than stranding the battle
        until midnight.
        """
        return await self._stamp_and_settle_unrated(
            battle_id, lease_token, reason, "budget-exhausted"
        )

    async def settle_injection_flagged(
        self, battle_id: str, lease_token: str
    ) -> RatingChange | None:
        """Settle UNRATED when injection cannot be pinned to ONE side (F3).

        Used only when BOTH fighters injected (or the attribution is ambiguous):
        with no clean winner to award, the battle completes UNRATED with
        ``INJECTION_STOP_REASON`` and no quorum — never silently dropped, and
        never rewarding either injector with a win. The single-injector case does
        NOT come here; it goes to :meth:`settle_injection_disqualified`.
        """
        return await self._stamp_and_settle_unrated(
            battle_id, lease_token, INJECTION_STOP_REASON, "injection-flagged"
        )

    async def settle_injection_disqualified(
        self, battle_id: str, lease_token: str, injecting_side: Side
    ) -> RatingChange | None:
        """Disqualify the ONE injecting side; the clean opponent wins (F3).

        THE anti-grief rule. Auto-UNRATING on any detected injection would be a
        denial primitive: a fighter about to lose could embed an injection to
        void the battle and rob the opponent of an earned rated win — rewarding
        the attacker. Instead, when exactly one side is caught (high-confidence,
        per the high-precision detector), that side is treated as the LOSER and
        the clean opponent WINS — rated if the battle was rated-eligible and the
        owners differ. Injecting is self-harming, never deny-all.

        The winner is FORCED (override_verdict), not derived: the panel never ran,
        so there is no honest vote to read. ``judging_stop_reason`` is
        deliberately NOT stamped — stamping it would force the battle UNRATED and
        re-open the very denial this method closes. The public ``verdict_reason``
        records the disqualification instead, so the outcome is auditable and the
        clean win still rates through settle_battle's normal rated gate.
        """
        winner_side = Side.B if injecting_side is Side.A else Side.A
        forced = PanelVerdict(
            winner=winner_side,
            is_tie=False,
            reason=(
                f"{INJECTION_STOP_REASON}: side {injecting_side.value} disqualified "
                f"for a judge-directed injection; side {winner_side.value} wins by default"
            ),
            votes=[],
        )
        return await self.settle_battle(battle_id, lease_token, override_verdict=forced)

    @staticmethod
    def _half_is_terminal(run: dict | None) -> bool:
        """Can this replicate half produce no further result?

        Terminal when the run completed (it has a real vote to collapse) or its
        own attempt budget is spent (it will never be reclaimed, so its silence is
        a definitive error). A run still pending/running/failed with attempts
        left is reclaimable — a transient throttle or an unparsable reply, not a
        verdict — and must not be
        collapsed yet. A missing row is treated as terminal so the panel cannot
        loop forever on a slot that no longer exists.
        """
        if run is None:
            return True
        if str(run["status"]) == JudgeRunStatus.COMPLETED.value:
            return True
        return int(run["attempt_count"]) >= JUDGE_RUN_MAX_ATTEMPTS

    async def _run_one_half(
        self,
        battle_id: str,
        battle: dict,
        seed: str,
        order: PresentedOrder,
        rubric: list,
        allowed: set[str],
        submission_a: str | None,
        submission_b: str | None,
        model: JudgeModel,
        system_prompt: str,
        battle_lease_token: str | None = None,
        budget: BattleJudgeBudgetService | None = None,
    ) -> JudgeRunResult:
        """One raw run: claim the slot, reserve a budget unit, call, write back.

        ``model`` is this replicate's assigned judge model + credentials, and
        ``system_prompt`` its assigned paraphrase (Track 2 diversity). Both are
        threaded through so the run row's judge_ref, the budget ledger's
        provider/model, the system message and the wire model all reflect the
        model that actually judged this replicate.

        When ``budget`` is supplied (the production path) a call unit is reserved
        in an independent transaction BEFORE the provider request — refused at the
        per-battle product cap or a spent daily budget, which raises
        JudgeBudgetExhausted so the panel settles UNRATED rather than spending a
        13th call. A lost lease refuses without raising (a stale worker, handled
        like the existing lost-row path). When ``budget`` is None (unit tests that
        mock the provider) enforcement is skipped and behaviour is unchanged.
        """
        run_id = await self.repo.create_judge_run(
            battle_id=battle_id,
            judge_kind=JUDGE_KIND_LLM,
            judge_ref=model.model_id,
            replicate_seed=seed,
            presented_order=order.value,
        )
        await self.db.commit()

        if run_id is None:
            # The slot exists: either finished (reuse its verdict — never call
            # the model twice for a slot that already answered) or held by a
            # live worker.
            existing = next(
                (
                    r
                    for r in await self.repo.list_judge_runs(battle_id)
                    if str(r["replicate_seed"]) == seed and str(r["presented_order"]) == order.value
                ),
                None,
            )
            if existing and existing["vote"]:
                return JudgeRunResult(
                    presented_order=order,
                    vote=Vote(existing["vote"]),
                    confidence=existing["confidence"],
                    reasoning=existing["reasoning"],
                )
            if existing is None:
                return JudgeRunResult(presented_order=order, vote=Vote.ERROR)
            run_id = str(existing["id"])

        run_token = str(uuid.uuid4())
        claimed = await self.repo.claim_judge_run(run_id, run_token, JUDGE_RUN_LEASE_SECONDS)
        await self.db.commit()
        if claimed is None:
            # Someone else holds it, or the attempt ceiling is spent. Not our
            # work; report an error half rather than calling anyway.
            return JudgeRunResult(presented_order=order, vote=Vote.ERROR)

        # Reserve a call unit BEFORE transmitting (V68 B). An independent
        # transaction that commits before the request, so a crash after
        # reservation still consumes the unit and a terminal budget refusal
        # settles the panel UNRATED rather than authorizing a 13th call.
        reservation = None
        if budget is not None:
            # Check the breaker immediately before reserving (V68 B5), so an
            # already-running panel stops BETWEEN halves the moment the breaker
            # trips. Distinct from budget exhaustion: this is transient, so the
            # battle stays 'judging' for a later pass rather than settling.
            if await breaker_is_open():
                raise JudgeBreakerOpen("judge breaker open")
            reservation = await budget.reserve_call(
                battle_id=battle_id,
                judge_run_id=run_id,
                battle_lease_token=str(battle_lease_token),
                run_lease_token=run_token,
                owner_a_user_id=str(battle["agent_a_owner_snapshot"]),
                owner_b_user_id=str(battle["agent_b_owner_snapshot"]),
                provider=model.provider,
                model=model.model_id,
            )
            if not reservation.granted:
                if reservation.reason in STOP_REASONS:
                    # Terminal for this budget period — stop the whole panel.
                    raise JudgeBudgetExhausted(reservation.reason)
                # Stale lease: no right to spend. Treat as a lost row.
                return JudgeRunResult(presented_order=order, vote=Vote.ERROR)

        payload, label_map = build_judge_payload(
            task_prompt=battle["task_prompt_snapshot"],
            rubric=rubric,
            submission_a=submission_a,
            submission_b=submission_b,
            presented_order=order,
        )
        messages = build_judge_messages(payload, system_prompt=system_prompt)

        # A reserved ledger row must ALWAYS be settled, or it stays 'reserved'
        # forever and inflates the per-battle attempt count (F7). The outer
        # finally is the backstop for cancellation / parse / write / aclose
        # failures that the inner except cannot see; settle_call is idempotent
        # (WHERE status='reserved'), so a double-settle is harmless.
        reservation_settled = False
        try:
            http = self.http or httpx.AsyncClient()
            try:
                if budget is not None:
                    await breaker_record_attempt()
                raw = await call_judge_model(
                    client=http,
                    base_url=model.base_url,
                    api_key=model.api_key,
                    messages=messages,
                    seed=seed,
                    gate=self.gate,
                    wire_model=model.wire_model,
                )
            except JudgeTransportError as exc:
                logger.warning("judge run {} failed: {}", run_id, exc)
                if budget is not None:
                    # Feed the breaker: a permanent (balance/auth) failure opens
                    # it at once, transient failures only on threshold.
                    await breaker_record_failure(permanent=exc.permanent)
                if reservation is not None:
                    await budget.settle_call(
                        reservation.ledger_id,
                        succeeded=False,
                        error_class=type(exc).__name__,
                    )
                    reservation_settled = True
                return JudgeRunResult(presented_order=order, vote=Vote.ERROR)
            finally:
                if self.http is None:
                    await http.aclose()

            # The call returned, so the unit was spent regardless of what happens
            # to the parsed result below.
            if reservation is not None:
                await budget.settle_call(
                    reservation.ledger_id, succeeded=True, http_status=200
                )
                reservation_settled = True

            parsed = parse_judge_response(raw, label_map, allowed)
            if parsed is None:
                # UNPARSABLE — the model did not answer our contract. That is a
                # failed call, not a verdict, so it is RETRYABLE exactly like a
                # transport error: release the slot back to 'failed' (its
                # attempt is already spent, and the per-battle cap bounds the
                # retries) and let a later pass ask again. Writing it as a
                # terminal abstention is what silently cost a live battle its
                # winner: one unparsable half froze its replicate out of the
                # quorum while the other four half-votes were unanimous.
                #
                # A DELIBERATE abstention is the other branch below: it parses,
                # so it completes the run and is never re-asked.
                await self.repo.fail_judge_run(run_id, run_token)
                await self.db.commit()
                logger.warning(
                    "judge run {} unparsable reply: released for retry", run_id
                )
                return JudgeRunResult(presented_order=order, vote=Vote.ABSTAIN)

            result = JudgeRunResult(
                presented_order=order,
                vote=parsed.vote,
                confidence=parsed.confidence,
                reasoning=parsed.reasoning,
                scores=parsed.scores,
            )

            # The token check is the whole point: if our lease lapsed while z.ai
            # was thinking, someone else owns this slot now and the answer is
            # discarded.
            written = await self.repo.complete_judge_run(
                run_id=run_id,
                lease_token=run_token,
                vote=result.vote.value,
                confidence=result.confidence,
                reasoning=result.reasoning,
                scores=result.scores,
            )
            await self.db.commit()
            if written is None:
                logger.info("judge run {} result discarded: lost the row", run_id)
                return JudgeRunResult(presented_order=order, vote=Vote.ERROR)

            return result
        finally:
            # Backstop: any exit that did not already settle the reservation
            # (cancellation, parse/write error, aclose failure) marks the unit
            # failed so it never lingers 'reserved' and over-counts the cap.
            if reservation is not None and not reservation_settled:
                await budget.settle_call(
                    reservation.ledger_id, succeeded=False, error_class="unsettled"
                )


# --- owner notifications for terminal transitions --------------------------
# The notification is best-effort: the state transition is the business
# decision and is already durable by the time these run, so a delivery failure
# is logged and swallowed here (and ONLY here) rather than rolling back a
# completed / expired / aborted battle.

_BATTLE_NOTIFY_SOURCE_TYPE = "battle_notification"


def _battle_result_title(battle_id: str, side: Side, winner: str | None) -> str:
    """Owner-facing title for a finished battle, from ONE fighter's viewpoint.

    winner is the battle row's ``winner`` column ("a"/"b"/"tie") or None. The
    two None-shaped outcomes are kept DISTINCT, exactly as the machine keeps
    them (see BattleRunner._verdict_to_winner): an explicit "tie" means the panel
    reached quorum ON a draw and is a real "ничья"; None means the panel reached
    NO quorum at all, which is not evidence of equality — it is "результат не
    определён", so it must never be reported as a draw.
    """
    if winner == Winner.TIE.value:
        outcome = "ничья"
    elif winner is None:
        outcome = "результат не определён: жюри не набрало кворум"
    elif winner == side.value:
        outcome = "победа"
    else:
        outcome = "поражение"
    return f"Бой завершён — {outcome} (бой {battle_id})"


async def _notify_battle_owners(
    session: AsyncSession,
    battle_id: str,
    recipients: list[tuple[str, str, str]],
) -> None:
    """Best-effort owner notifications AFTER a terminal transition committed.

    ``recipients`` is a list of ``(agent_id, task_type, title)``. Delivery goes
    through the platform's existing notification-task mechanism
    (AgentService.create_notification_task -> tasks row + heartbeat/realtime
    push), so battle results surface exactly where owners already read GitHub
    and DM notifications. A per-(agent, task_type) source_key dedups a re-run.

    Each recipient is delivered in its OWN transaction on an already-clean
    session. This is deliberate: a failure delivering to B must be able to
    neither roll back A's already-committed notification nor the terminal
    transition (which committed before this call). Every failure is logged and
    swallowed — the transition is durable and the notification is best-effort,
    so a notify error must never undo the state change. Never re-raises.

    Honest accounting of what is NOT guaranteed. create_notification_task
    couples the durable insert with the realtime deliver_event, and this module
    may not edit that service, so per-recipient isolation is the strongest fix
    available here. The accepted residual risks are:

    * Phantom realtime push on rollback — deliver_event fires INSIDE
      create_notification_task, before this per-recipient commit. If that commit
      fails, a websocket push went out with no durable tasks row behind it. It
      self-heals: the agent re-reads its notifications from the tasks table on
      the next heartbeat, so a push with no row simply shows nothing.
    * No durable dedup CONSTRAINT — dedup is a pending-task lookup, not a unique
      index, so two terminal passes racing on one battle could double-insert.
      Not reachable today: the reconciler is single-writer per battle via the
      row lease, and this runs after that write.
    * No retry after a crash between the transition commit and this call — the
      notification is simply lost. Battle state stays correct and the owner can
      still read the outcome from the battle row / verdict endpoint.
    """
    if not recipients:
        return
    for agent_id, task_type, title in recipients:
        try:
            # Import AND construction live INSIDE the guard: an import error or a
            # constructor failure must be swallowed like any other, or it would
            # escape and abort the caller (in reap_once, the rest of the reaper
            # pass) AFTER the terminal transition already committed. The lazy
            # import (cached after first use) also keeps module load cheap and
            # sidesteps any import cycle with the heavy AgentService graph.
            from app.services.agent_service import AgentService  # noqa: PLC0415

            svc = AgentService(session)
            await svc.create_notification_task(
                assigned_to_agent_id=agent_id,
                task_type=task_type,
                title=title,
                project_id=None,
                source_ref=f"/battles/{battle_id}",
                source_key=f"battle:{battle_id}:{task_type}",
                priority="medium",
                source_type=_BATTLE_NOTIFY_SOURCE_TYPE,
            )
            await session.commit()
        except Exception as exc:
            logger.warning(
                "battle {} owner notification to {} failed (transition durable): {}",
                battle_id,
                agent_id,
                exc,
            )
            await session.rollback()


async def _judge_and_settle(
    session_factory,
    gate: LLMGate,
    battle_id: str,
    token: str,
    api_key: str,
    base_url: str,
    counts: dict[str, int],
    budget: BattleJudgeBudgetService | None = None,
) -> None:
    """Run the panel, then settle IFF every replicate reached a terminal vote.

    Shared by the running phase (after close_deadline) and the judging resume
    phase. settle fires only once all REPLICATE_COUNT collapsed votes exist: a
    replicate still waiting on a reclaimable half persists nothing and leaves the
    battle 'judging' for a later pass, rather than settling a partial panel into a
    no-quorum verdict that would complete the battle unrated. run_judge_panel
    already committed whatever it persisted, so the not-yet-complete branch simply
    leaves the (renewed) lease in place for the next reclaim.
    """
    async with session_factory() as session:
        runner = BattleRunner(session, gate)
        try:
            await runner.run_judge_panel(battle_id, api_key, base_url, token, budget=budget)
            judgements = await runner.repo.list_judgements(battle_id)
            if len(judgements) >= REPLICATE_COUNT:
                if await runner.settle_battle(battle_id, token) is not None:
                    counts["settled"] += 1
        except JudgeInjectionSuspected as exc:
            # A submission carried judge-directed injection shapes. The panel never
            # ran, so no paid call was spent. Attribution decides the outcome (F3):
            # if exactly ONE side injected it is disqualified and the clean opponent
            # wins (rated when eligible) — injecting is self-harming, never a way to
            # deny the opponent's earned win. Only when BOTH sides injected (or it
            # cannot be pinned to one) is the battle UNRATED. Pattern detail is on
            # the exception for the log; never the submission text.
            injecting_sides = {f.side for f in exc.findings}
            await session.rollback()
            if len(injecting_sides) == 1:
                loser = next(iter(injecting_sides))
                logger.warning(
                    "battle {} injection by side {} ({}): disqualifying injector, "
                    "opponent wins",
                    battle_id, loser.value, exc,
                )
                settled = await runner.settle_injection_disqualified(battle_id, token, loser)
            else:
                logger.warning(
                    "battle {} injection by both/ambiguous ({}): settling unrated",
                    battle_id, exc,
                )
                settled = await runner.settle_injection_flagged(battle_id, token)
            if settled is not None:
                counts["settled"] += 1
        except JudgeBudgetExhausted as exc:
            # The budget for this period is spent: settle UNRATED now rather than
            # stranding the battle. Free lifecycle phases keep running regardless.
            logger.warning(
                "battle {} judge budget exhausted ({}): settling unrated",
                battle_id, exc.reason,
            )
            await session.rollback()
            if await runner.settle_budget_exhausted(battle_id, token, exc.reason) is not None:
                counts["settled"] += 1
        except JudgeBreakerOpen:
            # Transient breaker trip: leave the battle in 'judging' for a later
            # pass (do NOT settle, do NOT strand). The panel can still complete
            # once the breaker closes. Whatever partial halves already committed
            # stay, and the renewed lease keeps the row reclaimable.
            logger.warning(
                "battle {} judging paused: judge breaker open", battle_id
            )
            await session.rollback()
        except Exception as exc:
            logger.exception("judging failed for battle {}: {}", battle_id, exc)
            await session.rollback()


async def reap_once(session_factory, provider: dict | None = None) -> dict[str, int]:
    """Route abandoned battles and stale reservations to terminal states.

    mark_expired, mark_aborted and delete_expired_reservations each shipped with
    zero callers, so a challenge nobody answered before its deadline, a pre-start
    battle that burned its whole claim budget, and reservations left behind by any
    path all lived forever. This is their one driver, run once per reconcile pass:

    * expired          — challenge_pending/accepted/reserved past
      challenge_expires_at;
    * aborted          — pre-'running' rows whose claim attempts are spent (the
      routing claim_battles_for_reconcile's own docstring promises);
    * stranded_settled — judging battles whose attempt budget is spent (the escape
      hatch below);
    * reaped           — reservations whose wall clock passed AND whose battle is
      not still live (delete_expired_reservations skips running/judging rows).

    ``provider`` gates ONLY the stranded-judging escape hatch. Every other reap
    (expire, abort, release reservations) is free/DB-only and runs regardless.
    The escape hatch mints an honest no-quorum for a panel that genuinely
    exhausted its budget WITH a working provider; during a provider outage
    (``provider is None``) the same battle must WAIT, not be settled unrated —
    a later provider-backed pass could still judge it once the outage clears.
    (When the provider returns: attempt >= RUNNING_MAX_ATTEMPTS -> escape hatch
    fires -> no-quorum; attempt < ceiling -> the judging-resume phase judges it.)
    Default ``None`` = do not run the escape hatch; callers that want it must
    pass a provider explicitly.

    Each terminal write commits in ITS OWN transaction, not one pass-wide one: a
    finder returns a bounded batch (LIMIT RECONCILE_BATCH), and per-item commits
    mean one row that raises does not roll back the rows already reaped this pass.
    Reservations are released in the SAME transaction as each terminal write, so a
    reaped battle never leaves a fighter pinned. A row already terminal is skipped
    by the CAS inside mark_expired/mark_aborted/claim_stranded_judging, which keeps
    the reaper idempotent — a re-run over the same backlog is a no-op.
    """
    counts = {"expired": 0, "aborted": 0, "reservations_reaped": 0, "stranded_settled": 0}

    async with session_factory() as session:
        repo = BattleRepository(session)
        expired_ids = await repo.find_expired_battle_ids(RECONCILE_BATCH)
        exhausted_ids = await repo.find_attempt_exhausted_battle_ids(
            POLL_MAX_ATTEMPTS, RECONCILE_BATCH
        )
        # Escape hatch is provider-gated: during an outage a stranded judging
        # battle must wait for the provider, not be finalized no-quorum now.
        stranded_ids = (
            await repo.find_stranded_judging_battle_ids(RUNNING_MAX_ATTEMPTS, RECONCILE_BATCH)
            if provider is not None
            else []
        )

    for battle_id in expired_ids:
        async with session_factory() as session:
            repo = BattleRepository(session)
            expired = None
            try:
                expired = await repo.mark_expired(battle_id)
                if expired is not None:
                    await repo.release_reservations(battle_id)
                    counts["expired"] += 1
                await session.commit()
            except Exception as exc:
                logger.exception("reaper: expiring battle {} failed: {}", battle_id, exc)
                await session.rollback()
                expired = None
            if expired is not None:
                # Only the challenger's owner is notified: an expired challenge
                # was never answered, so there may be no opponent at all.
                title = f"Вызов истёк (бой {battle_id})"
                await _notify_battle_owners(
                    session,
                    battle_id,
                    [(str(expired["agent_a_id"]), "battle_expired", title)],
                )

    for battle_id in exhausted_ids:
        async with session_factory() as session:
            repo = BattleRepository(session)
            aborted = None
            try:
                aborted = await repo.mark_aborted(
                    battle_id, "reconciler: claim attempts exhausted"
                )
                if aborted is not None:
                    await repo.release_reservations(battle_id)
                    counts["aborted"] += 1
                await session.commit()
            except Exception as exc:
                logger.exception("reaper: aborting battle {} failed: {}", battle_id, exc)
                await session.rollback()
                aborted = None
            if aborted is not None:
                title = f"Бой прерван (бой {battle_id})"
                recipients = [(str(aborted["agent_a_id"]), "battle_aborted", title)]
                if aborted["agent_b_id"]:
                    recipients.append((str(aborted["agent_b_id"]), "battle_aborted", title))
                await _notify_battle_owners(session, battle_id, recipients)

    # Escape hatch: a judging battle whose attempt budget is spent must reach a
    # terminal state, never sit unclaimable in 'judging' forever with its fighters
    # pinned. Re-lease it, collapse the still-open replicates to error votes (the
    # budget is genuinely exhausted — the panel will never answer), then settle:
    # error votes leave the quorum denominator, so this finalizes to no-quorum,
    # completed and UNRATED. That is the honest outcome — a broken judge must not
    # mint tie-Elo. settle_battle never calls the gate, so gate=None is correct.
    for battle_id in stranded_ids:
        async with session_factory() as session:
            runner = BattleRunner(session, gate=None)
            try:
                token = str(uuid.uuid4())
                claimed = await runner.repo.claim_stranded_judging(
                    battle_id, token, BATTLE_LEASE_SECONDS, RUNNING_MAX_ATTEMPTS
                )
                if claimed is None:
                    await session.rollback()
                    continue
                await runner.collapse_open_replicates_to_error(battle_id)
                await session.commit()
                if await runner.settle_battle(battle_id, token) is not None:
                    counts["stranded_settled"] += 1
            except Exception as exc:
                logger.exception("reaper: settling stranded battle {} failed: {}", battle_id, exc)
                await session.rollback()

    async with session_factory() as session:
        repo = BattleRepository(session)
        try:
            counts["reservations_reaped"] = len(
                await repo.delete_expired_reservations(RECONCILE_BATCH)
            )
            await session.commit()
        except Exception as exc:
            logger.exception("reaper: reaping reservations failed: {}", exc)
            await session.rollback()

    return counts


async def reconcile_once(
    session_factory,
    gate: LLMGate,
    provider: dict | None,
) -> dict[str, int]:
    """One short reconciler pass over the WHOLE chain. BattleRunTask calls this.

    Drives accepted -> reserved -> queued -> running -> judging -> completed. It
    is the only driver: every one of those transitions was written, tested and
    left with no caller, so a battle could be created, accepted and acked and
    then sit forever. Details without a shaft.

    Deliberately a function of claimed ROWS, not of a global lock: each phase
    claims a bounded batch, takes ONE step per battle, and returns. Nothing here
    holds a battle across passes, so losing the scheduler lease mid-pass costs at
    most the in-flight steps — which the row tokens then reject.

    Phases run oldest-first and independently, so a battle stuck waiting for an
    ACK cannot delay one that is ready to start.

    ``provider`` (``{"api_key", "base_url"}`` or ``None``) is the ONLY paid
    dependency, and it gates ONLY the judge panel. Every other phase — arm,
    admit, start, close_deadline (running -> judging is FREE), and the whole
    reaper — is DB-only and MUST run every pass regardless. So a provider outage
    (key unset/rotated/geo-blocked) does NOT freeze the lifecycle or stop
    cleanup: battles still advance up to 'judging' and expired challenges /
    stranded reservations are still reaped. When ``provider is None`` the two
    money phases (the panel after close_deadline, and the judging resume) are
    skipped and NOT claimed — a battle that reached 'judging' simply waits for
    the provider to return rather than burning its attempt budget toward abort.
    """
    counts = {"armed": 0, "queued": 0, "started": 0, "judged": 0, "settled": 0}
    token = str(uuid.uuid4())
    api_key = provider["api_key"] if provider is not None else None
    base_url = provider["base_url"] if provider is not None else None
    # Only the paid judge phases need the budget ledger; it shares the reconciler
    # session factory but opens its own short transactions per reservation.
    budget = BattleJudgeBudgetService(session_factory) if provider is not None else None

    async def claim(status: BattleStatus, max_attempts: int, lease_seconds: int) -> list[dict]:
        """Claim a bounded batch of one status. Oldest first, skipping held rows.

        FOR UPDATE SKIP LOCKED inside claim_battles_for_reconcile is what stops
        one slow battle from blocking the rest: a row another worker holds is
        stepped over, not waited on. The ordering (queued_at NULLS FIRST, then
        challenged_at) is the anti-starvation rule and is what V66:246 indexes.
        """
        async with session_factory() as session:
            claimed = await BattleRepository(session).claim_battles_for_reconcile(
                status=status,
                lease_token=token,
                lease_seconds=lease_seconds,
                limit=RECONCILE_BATCH,
                max_attempts=max_attempts,
            )
            await session.commit()
            return claimed

    async def step(battle: dict, what: str, fn) -> bool:
        """Run one battle's step in its own session; never let it kill the pass."""
        async with session_factory() as session:
            runner = BattleRunner(session, gate)
            try:
                return await fn(runner, battle)
            except Exception as exc:
                logger.exception("{} failed for battle {}: {}", what, battle["id"], exc)
                await session.rollback()
                return False

    # 1. accepted -> reserved, and push the ready-checks. Cheap CAS + transport.
    for battle in await claim(BattleStatus.ACCEPTED, POLL_MAX_ATTEMPTS, POLL_LEASE_SECONDS):
        if await step(battle, "arm", lambda r, b: r.arm_accepted(b)):
            counts["armed"] += 1

    # 2. reserved -> queued AND bind a task, once both fighters have acked.
    #    Lease-FENCED (V67): binding chooses and cools down a concrete task, so
    #    unlike the other cheap phases the reserved claim holds a real short
    #    lease and the claim token is passed into admit_reserved — only the
    #    worker holding it may bind the row.
    for battle in await claim(BattleStatus.RESERVED, POLL_MAX_ATTEMPTS, TASK_BIND_LEASE_SECONDS):
        if await step(battle, "admit", lambda r, b: r.admit_reserved(b, token)):
            counts["queued"] += 1

    # 3. queued -> running, with both battle_turn rows. Cheap CAS + transport.
    for battle in await claim(BattleStatus.QUEUED, POLL_MAX_ATTEMPTS, POLL_LEASE_SECONDS):
        if await step(battle, "start", lambda r, b: r.start_queued(b, token)):
            counts["started"] += 1

    # 4. running -> judging. The ONLY phases that spend money (this and the
    #    judging resume below), so both keep claim_battles_for_reconcile's strict
    #    default attempt ceiling.
    #    close_deadline itself is FREE and always runs; only the panel that
    #    follows spends money, so it is gated on the provider. A battle whose
    #    deadline passed still transitions running -> judging without a provider
    #    and waits there for one.
    for battle in await claim(BattleStatus.RUNNING, RUNNING_MAX_ATTEMPTS, BATTLE_LEASE_SECONDS):
        battle_id = str(battle["id"])
        if not await step(battle, "reconcile", lambda r, b: r.close_deadline(str(b["id"]), token)):
            continue
        counts["judged"] += 1
        if provider is not None:
            await _judge_and_settle(
                session_factory, gate, battle_id, token, api_key, base_url, counts, budget
            )

    # 5. judging -> completed. Resume battles stranded in judging by a crash
    #    between mark_judging's commit and settle: run_judge_panel and
    #    settle_battle are both idempotent (slot unique keys and the finalize
    #    CAS), so re-running them completes the battle exactly once. Without this
    #    phase such a battle sits in 'judging' forever — the running phase above
    #    already transitioned it, so nothing would ever claim it again.
    #    Money phase: skipped entirely (not even claimed, so no attempt is burnt
    #    toward the stranded-abort ceiling) when no provider is available.
    if provider is not None:
        for battle in await claim(BattleStatus.JUDGING, RUNNING_MAX_ATTEMPTS, BATTLE_LEASE_SECONDS):
            await _judge_and_settle(
                session_factory, gate, str(battle["id"]), token, api_key, base_url, counts, budget
            )

    counts.update(await reap_once(session_factory, provider))
    return counts
