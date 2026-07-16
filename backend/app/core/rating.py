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

# Score awarded to the side under consideration, by outcome.
_SCORE_WIN = 1.0
_SCORE_DRAW = 0.5
_SCORE_LOSS = 0.0


def expected_score(rating_a: int, rating_b: int) -> float:
    """P(A scores) under the Elo model: ``1 / (1 + 10^((Rb-Ra)/400))``.

    Symmetric by construction: ``expected_score(a, b) + expected_score(b, a)``
    is exactly 1.0, which is what makes the zero-sum property of
    :func:`apply_battle_result` hold rather than merely nearly hold.
    """
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / _ELO_SCALE))


def new_rating(rating: int, expected: float, score: float, k: int = K_FACTOR) -> int:
    """``R' = R + K*(S - E)``, rounded to the integer the column stores.

    Rounding happens here, once, rather than at the call site: the caller
    persists what this returns, so the rounded value IS the rating. Rounding
    later — or twice — is how a stored rating drifts from the one the maths
    justified.
    """
    return round(rating + k * (score - expected))


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

    expected_a = expected_score(rating_a, rating_b)

    return RatingChange(
        a_before=rating_a,
        b_before=rating_b,
        a_after=new_rating(rating_a, expected_a, score_a, k=k),
        # 1.0 - expected_a rather than expected_score(rating_b, rating_a): the
        # two are equal in exact arithmetic, and using the complement makes the
        # sum-preservation property exact in floating point too.
        b_after=new_rating(rating_b, 1.0 - expected_a, 1.0 - score_a, k=k),
        applied=True,
    )
