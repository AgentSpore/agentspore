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

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rating import RatingChange, apply_battle_result
from app.repositories.agent_event_repo import AgentEventRepository
from app.repositories.battle_repo import BattleRepository, ReservationConflictError
from app.schemas.battles import BattleStatus, PresentedOrder, Side, Vote, Winner
from app.services.battle_judges import (
    JUDGE_KIND_LLM,
    JUDGE_MODEL,
    PRESENTED_ORDERS,
    REPLICATE_COUNT,
    CollapsedVote,
    JudgeRunResult,
    JudgeTransportError,
    build_judge_messages,
    build_judge_payload,
    call_judge_model,
    collapse_pair,
    parse_judge_response,
    replicate_seed,
    resolve_verdict,
    rubric_keys,
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

    async def settle_battle(self, battle_id: str, lease_token: str) -> RatingChange | None:
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

        Returns None when this worker lost the race, which is a normal outcome
        and not an error. Returns an unapplied RatingChange when the battle
        legitimately rates nothing (no quorum, or same-owner self-play).
        """
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
        change = apply_battle_result(
            fighters["elo_a"],
            fighters["elo_b"],
            winner,
            same_owner=same_owner,
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

    async def admit_reserved(self, battle: dict) -> bool:
        """reserved -> queued once both ready-ACKs are in. False = not yet.

        Delegates the decision to BattleService.try_queue -> admit_to_queue,
        which re-proves consent, eligibility, ownership, both live reservations
        and both exact current-generation ACK ids in ONE statement. Nothing is
        re-checked here, because a second opinion computed in Python would be a
        different, weaker predicate evaluated at a different instant.

        False is the ordinary case, not an error: agents have not acked yet. The
        battle stays claimable and the next pass asks again, until the readiness
        lease lapses and the battle is released back to 'accepted' — freeing both
        fighters rather than pinning them to a battle nobody is answering.
        """
        battle_id = str(battle["id"])
        service = BattleService(self.db)

        queued = await service.try_queue(battle_id, battle["readiness_generation"])
        if queued is not None:
            await self.db.commit()
            logger.info("battle {} queued: both fighters acked", battle_id)
            return True

        # Not admissible. If the lease has lapsed, stop waiting and let both
        # fighters go — in the SAME transaction as the state change.
        released = await service.release_expired_readiness(battle_id)
        await self.db.commit()
        if released is not None:
            logger.info("battle {} readiness lapsed: reservations released", battle_id)
        return False

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
        """Fill in any missing final submission, then running -> judging.

        The synthetic submission goes in FIRST and in the same transaction as
        the transition: ``mark_judging`` only fires once the wall clock ran out
        or both sides finalised, so a battle judged with a missing side must
        have that side's silence recorded as a real, truncated answer.

        ``add_submission`` returning False means the fighter's real answer beat
        us to the slot by a hair. That is not an error — the partial unique
        index arbitrated it, and their answer wins.
        """
        battle = await self.repo.get(battle_id)
        if battle is None or battle["status"] != BattleStatus.RUNNING.value:
            await self.db.rollback()
            return False

        submitted = {
            (str(s["side"]), bool(s["is_final"]))
            for s in await self.repo.list_submissions(battle_id)
        }
        finalised_sides = {side for side, is_final in submitted if is_final}

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
                )

        judging = await self.repo.mark_judging(battle_id, lease_token)
        if judging is None:
            await self.db.rollback()
            return False

        await self.db.commit()
        return True

    # -- judging ------------------------------------------------------------

    async def run_judge_panel(
        self, battle_id: str, api_key: str, base_url: str
    ) -> list[CollapsedVote]:
        """Run three paired replicates and persist their collapsed votes.

        Each raw run is its own claimed row, so a restart resumes rather than
        restarting: slots already completed are skipped by the unique key, and
        only the missing halves are re-called. This is what makes
        "reconciliation after restart produces the same state" true.

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

        collapsed: list[CollapsedVote] = []
        for replicate_no in range(REPLICATE_COUNT):
            seed = replicate_seed(battle_id, replicate_no)
            halves: list[JudgeRunResult] = []

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
                        api_key=api_key,
                        base_url=base_url,
                    )
                )

            vote = collapse_pair(halves[0], halves[1], seed)
            collapsed.append(vote)

            # One collapsed vote per replicate — the unique key without
            # presented_order makes a second attempt a no-op rather than a
            # second vote.
            await self.repo.upsert_judgement(
                battle_id=battle_id,
                judge_kind=JUDGE_KIND_LLM,
                judge_ref=JUDGE_MODEL,
                replicate_seed=seed,
                vote=vote.vote.value,
                confidence=vote.confidence,
                reasoning=vote.reasoning,
                scores=vote.scores,
                position_sensitive=vote.position_sensitive,
            )
            await self.db.commit()

        return collapsed

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
        api_key: str,
        base_url: str,
    ) -> JudgeRunResult:
        """One raw run: claim the slot, call, write back under the token."""
        run_id = await self.repo.create_judge_run(
            battle_id=battle_id,
            judge_kind=JUDGE_KIND_LLM,
            judge_ref=JUDGE_MODEL,
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

        payload, label_map = build_judge_payload(
            task_prompt=battle["task_prompt_snapshot"],
            rubric=rubric,
            submission_a=submission_a,
            submission_b=submission_b,
            presented_order=order,
        )
        messages = build_judge_messages(payload)

        http = self.http or httpx.AsyncClient()
        try:
            raw = await call_judge_model(
                client=http,
                base_url=base_url,
                api_key=api_key,
                messages=messages,
                seed=seed,
                gate=self.gate,
            )
        except JudgeTransportError as exc:
            logger.warning("judge run {} failed: {}", run_id, exc)
            return JudgeRunResult(presented_order=order, vote=Vote.ERROR)
        finally:
            if self.http is None:
                await http.aclose()

        parsed = parse_judge_response(raw, label_map, allowed)
        if parsed is None:
            # Malformed output is an ABSTENTION. Never a tie: a broken judge
            # must not mint tie-Elo.
            result = JudgeRunResult(presented_order=order, vote=Vote.ABSTAIN)
        else:
            result = JudgeRunResult(
                presented_order=order,
                vote=parsed.vote,
                confidence=parsed.confidence,
                reasoning=parsed.reasoning,
                scores=parsed.scores,
            )

        # The token check is the whole point: if our lease lapsed while z.ai was
        # thinking, someone else owns this slot now and our answer is discarded.
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


async def reconcile_once(
    session_factory,
    gate: LLMGate,
    api_key: str,
    base_url: str,
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
    """
    counts = {"armed": 0, "queued": 0, "started": 0, "judged": 0, "settled": 0}
    token = str(uuid.uuid4())

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

    # 2. reserved -> queued, once both fighters have acked. Cheap CAS, polled.
    for battle in await claim(BattleStatus.RESERVED, POLL_MAX_ATTEMPTS, POLL_LEASE_SECONDS):
        if await step(battle, "admit", lambda r, b: r.admit_reserved(b)):
            counts["queued"] += 1

    # 3. queued -> running, with both battle_turn rows. Cheap CAS + transport.
    for battle in await claim(BattleStatus.QUEUED, POLL_MAX_ATTEMPTS, POLL_LEASE_SECONDS):
        if await step(battle, "start", lambda r, b: r.start_queued(b, token)):
            counts["started"] += 1

    # 4. running -> judging -> completed. The ONLY phase that spends money, so
    #    it keeps claim_battles_for_reconcile's strict default attempt ceiling.
    for battle in await claim(BattleStatus.RUNNING, RUNNING_MAX_ATTEMPTS, BATTLE_LEASE_SECONDS):
        battle_id = str(battle["id"])
        if not await step(battle, "reconcile", lambda r, b: r.close_deadline(str(b["id"]), token)):
            continue
        counts["judged"] += 1

        async with session_factory() as session:
            runner = BattleRunner(session, gate)
            try:
                await runner.run_judge_panel(battle_id, api_key, base_url)
                if await runner.settle_battle(battle_id, token) is not None:
                    counts["settled"] += 1
            except Exception as exc:
                logger.exception("judging failed for battle {}: {}", battle_id, exc)
                await session.rollback()

    return counts
