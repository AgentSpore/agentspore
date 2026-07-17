"""Elo rating — pure functions, no I/O.

This module is deliberately the whole of the rating maths and none of its
persistence. Every function here is total, deterministic and side-effect free,
which is what makes the properties in test_rating.py provable by themselves
rather than through a database.

Where the numbers are applied is a separate decision, and a load-bearing one:
the caller writes both agents' new ratings in the SAME transaction as
``battles.status -> 'completed'``, guarded by
``WHERE status='judging' AND finalized_at IS NULL AND lease_token=...``
(battle_repo.finalize). That guard — not anything in this file — is what makes
"Elo is applied exactly once" true when two finalizers race.

Two cases produce no rating change at all, and both are anti-farming rules
rather than maths:

* **Same-owner self-play.** Rating is a claim about beating OTHER people's
  agents. If one owner controls both fighters they can hand themselves wins for
  the cost of inference, so the battle completes honestly, is recorded, and
  moves no rating and grants no badge.
* **No quorum** (``winner=None``). The judges abstained or errored, so nobody
  won. The battle completes with a verdict_reason saying so and both ratings
  stand. Inventing a winner to have something to apply is the one thing this
  module must never do — hence ``apply_battle_result`` takes ``Winner | None``
  and has no default.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.battles import Winner

# Starting rating for an agent that has never fought. Mirrors the
# ``agents.battle_elo`` DEFAULT in V66 — the database is the authority for a
# stored rating; this constant exists for the pure functions and their tests.
DEFAULT_ELO = 1200

# K-factor: the maximum a single battle can move a rating. 32 is the classic
# chess value and is chosen here for the property it gives us rather than for
# tradition: it bounds the swing, so no single battle — and no single lucky
# run of a stochastic judge panel — can dominate a rating history.
K_FACTOR = 32

# The scale constant of the Elo logistic curve: a 400-point gap means the
# stronger side is expected to score 10:1. Baked into the formula below.
_ELO_SCALE = 400.0

# Floor a stored rating may never fall below. It exists to keep every computed
# rating comfortably above the database's own guard: V66's
# ``agents_battle_elo_positive`` (and ``battle_elo_positive`` on the snapshot
# columns) require ``elo > 0``, so a rating that computes to <= 0 makes
# finalize's UPDATE violate the CHECK and the battle can never settle — it
# strands in 'judging' until the attempt cap aborts it and the true result is
# lost. A near-floor agent taking a heavy loss is exactly the case that reaches
# there: from a low rating the max K-swing can cross zero. 100 (not 1) is
# chosen so the floor sits well clear of the CHECK rather than one loss away
# from it, and so the leaderboard never shows a rating a rounding error from
# negative.
ELO_FLOOR = 100

# Ceiling a stored rating may never exceed. The battles.elo_* snapshots and
# agents.battle_elo are plain INT columns (V66), so a rating that grew past
# 2^31-1 would overflow the column and fail the settling UPDATE the same way a
# sub-zero one does — a stranded battle from the other end. 100_000 sits far
# above any rating real play produces (a lifetime of wins moves a rating by K a
# time) yet a wide margin below INT_MAX, so the clamp is a guard, never a limit
# a legitimate agent can reach.
ELO_CEILING = 100_000

# Largest magnitude the logistic exponent is allowed to take. ``10.0 ** 309``
# already exceeds the maximum double and raises OverflowError, so an extreme
# rating gap (a mis-seeded or adversarial rating) would crash expected_score
# outright. Past ~+-30 the logistic is 0.0 or 1.0 to full float precision, so
# clamping here removes the crash without changing any representable result.
_MAX_ELO_EXPONENT = 30.0

# Score awarded to the side under consideration, by outcome.
_SCORE_WIN = 1.0
_SCORE_DRAW = 0.5
_SCORE_LOSS = 0.0


def _clamp_rating(value: int) -> int:
    """Clamp a rating into the storable, CHECK-legal band [ELO_FLOOR, ELO_CEILING]."""
    if value < ELO_FLOOR:
        return ELO_FLOOR
    if value > ELO_CEILING:
        return ELO_CEILING
    return value


def expected_score(rating_a: int, rating_b: int) -> float:
    """P(A scores) under the Elo model: ``1 / (1 + 10^((Rb-Ra)/400))``.

    Symmetric by construction: ``expected_score(a, b) + expected_score(b, a)``
    is exactly 1.0, which is what makes the zero-sum property of
    :func:`apply_battle_result` hold rather than merely nearly hold.

    The exponent is clamped to ``+-_MAX_ELO_EXPONENT`` so an extreme rating gap
    cannot overflow ``10.0 ** x`` (which raises past ~309). The clamp only ever
    engages far beyond where the logistic has already saturated to 0 or 1, so
    it changes no result a real rating pair can produce — it only trades a
    crash for the 0.0/1.0 the maths was already heading to.
    """
    exponent = (rating_b - rating_a) / _ELO_SCALE
    if exponent > _MAX_ELO_EXPONENT:
        exponent = _MAX_ELO_EXPONENT
    elif exponent < -_MAX_ELO_EXPONENT:
        exponent = -_MAX_ELO_EXPONENT
    return 1.0 / (1.0 + 10.0**exponent)


def new_rating(rating: int, expected: float, score: float, k: int = K_FACTOR) -> int:
    """``R' = R + K*(S - E)``, rounded to the integer the column stores.

    Rounding happens here, once, rather than at the call site: the caller
    persists what this returns, so the rounded value IS the rating. Rounding
    later — or twice — is how a stored rating drifts from the one the maths
    justified.

    Clamped into ``[ELO_FLOOR, ELO_CEILING]``: the value returned is written
    straight into ``agents.battle_elo`` and the battle's ``elo_*_after``
    snapshot, both under a CHECK that demands ``> 0`` on a plain INT column. A
    near-floor loss can round to <= 0 and a runaway winner past 2^31-1, and
    either makes the settling UPDATE fail so the battle can never complete.
    Clamping both ends keeps every persisted rating storable.

    NOTE: this is the SINGLE-side clamp for direct callers. Pair settlement goes
    through :func:`apply_battle_result`, which clamps the pair TOGETHER so the
    floor/ceiling cannot mint or destroy points (zero-sum). Do not floor two
    ratings independently and expect them to still conserve.
    """
    return _clamp_rating(round(rating + k * (score - expected)))


@dataclass(frozen=True)
class RatingChange:
    """Both sides of one battle's rating move.

    Carries ``before`` as well as ``after`` because the battle row stores the
    snapshot of both (``elo_a_before``/``elo_a_after``): a rating is only
    auditable if the number it moved FROM is recorded next to the battle that
    moved it. ``applied=False`` means the ratings are unchanged and the equal
    before/after values are the honest record of a battle that moved nothing.
    """

    a_before: int
    b_before: int
    a_after: int
    b_after: int
    applied: bool

    @property
    def a_delta(self) -> int:
        """Signed change to A's rating. Zero when not applied."""
        return self.a_after - self.a_before

    @property
    def b_delta(self) -> int:
        """Signed change to B's rating. Zero when not applied."""
        return self.b_after - self.b_before


def apply_battle_result(
    rating_a: int,
    rating_b: int,
    winner: Winner | None,
    *,
    same_owner: bool = False,
    k: int = K_FACTOR,
) -> RatingChange:
    """Compute both fighters' new ratings for one finished battle.

    ``winner=None`` (no quorum) and ``same_owner=True`` both yield an unchanged,
    ``applied=False`` result rather than an exception: they are ordinary,
    expected outcomes of a battle that legitimately completed, and the caller
    still writes the snapshot and the 'completed' status for them.

    The two sides are computed from the SAME pair of "before" ratings, never
    sequentially — updating A first and feeding A's new rating into B's
    expectation would break the zero-sum property and silently mint rating.

    Zero-sum survives the floor/ceiling. The two ratings are clamped as a PAIR,
    not independently: the delta is rounded ONCE and applied as ``+delta`` to A
    and ``-delta`` to B, so the pair conserves exactly before any clamp. If a
    clamp then moves the bounded side (a near-floor loser, a near-ceiling
    winner), the SAME adjustment is transferred to the other side, so the winner
    gains only what the loser actually loses after the floor bites. Flooring the
    two ratings independently would mint points (both stay at the floor with a
    non-zero winner delta) — the bug this coupling exists to prevent.
    """
    if winner is None or same_owner:
        return RatingChange(
            a_before=rating_a,
            b_before=rating_b,
            a_after=rating_a,
            b_after=rating_b,
            applied=False,
        )

    if winner is Winner.A:
        score_a = _SCORE_WIN
    elif winner is Winner.B:
        score_a = _SCORE_LOSS
    else:
        score_a = _SCORE_DRAW

    # Clamp the BEFORE ratings into band first. The DB only guards elo > 0
    # (V66), so a legacy/out-of-band stored rating (e.g. a sub-floor 80) is
    # DB-legal. If BOTH sides breach the SAME bound it is impossible to keep
    # both in band AND conserve — the transfer below handles only one clamped
    # side, so two floored ratings would mint points. Clamping the inputs up
    # front guarantees at most ONE side breaches after the delta, so the pair
    # conserves EXACTLY for every DB-legal input. In-band inputs (all reachable
    # play — default 1200, and new writes are already clamped) are unchanged.
    rating_a = _clamp_rating(rating_a)
    rating_b = _clamp_rating(rating_b)

    expected_a = expected_score(rating_a, rating_b)
    # One rounded delta, applied as +/- so the pair conserves EXACTLY (integer
    # zero-sum) before any bound is considered. |delta| <= k, so neither the
    # K-bound nor the complement identity is disturbed.
    delta = round(k * (score_a - expected_a))
    raw_a = rating_a + delta
    raw_b = rating_b - delta

    a_after = _clamp_rating(raw_a)
    b_after = _clamp_rating(raw_b)
    # At most one side can breach a bound in a single battle (the deltas are
    # opposite-signed and |delta| <= k, while the bounds are far apart), so
    # transferring that one side's clamp adjustment to the other keeps the pair
    # exactly zero-sum without pushing the other side out of range.
    if a_after != raw_a:
        b_after = _clamp_rating(raw_b - (a_after - raw_a))
    elif b_after != raw_b:
        a_after = _clamp_rating(raw_a - (b_after - raw_b))

    return RatingChange(
        a_before=rating_a,
        b_before=rating_b,
        a_after=a_after,
        b_after=b_after,
        applied=True,
    )
