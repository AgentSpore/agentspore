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
from app.repositories.battle_repo import BattleRepository
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
    """One short reconciler pass. This is what BattleRunTask.run_once calls.

    Deliberately a function of claimed ROWS, not of a global lock: it claims a
    bounded batch, does one step each, and returns. Nothing here holds a battle
    across passes, so losing the scheduler lease mid-pass costs at most the
    in-flight steps — which the row tokens then reject.
    """
    counts = {"started": 0, "judged": 0, "settled": 0}
    token = str(uuid.uuid4())

    async with session_factory() as session:
        repo = BattleRepository(session)
        due = await repo.claim_battles_for_reconcile(
            status=BattleStatus.RUNNING,
            lease_token=token,
            lease_seconds=BATTLE_LEASE_SECONDS,
            limit=RECONCILE_BATCH,
        )
        await session.commit()

    for battle in due:
        battle_id = str(battle["id"])
        async with session_factory() as session:
            runner = BattleRunner(session, gate)
            try:
                if await runner.close_deadline(battle_id, token):
                    counts["judged"] += 1
            except Exception as exc:
                logger.exception("reconcile failed for battle {}: {}", battle_id, exc)
                await session.rollback()
                continue

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
