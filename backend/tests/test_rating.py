"""Tests for backend/app/core/rating.py — step 9's Elo maths.

The invariant under test, stated so it can be falsified:

    Elo rewards beating a stronger opponent more than a weaker one, never
    mints or destroys rating, and cannot move a rating further than K.

These are pure-function property tests: no Docker, no database, no mocks. That
is not a shortcut — rating.py has no I/O, so a database here would only prove
that Postgres stores integers. The "applied exactly once" half of the step-9
invariant is NOT provable at this layer and is not attempted here; it lives in
test_battles_api.py against a real transaction, because it is a property of the
CAS in battle_repo.finalize, not of this arithmetic.
"""

from __future__ import annotations

import pytest

from app.core.rating import (
    DEFAULT_ELO,
    ELO_CEILING,
    ELO_FLOOR,
    K_FACTOR,
    RatingChange,
    apply_battle_result,
    expected_score,
    new_rating,
)
from app.schemas.battles import Winner

# Rating pairs spanning the interesting shapes: equal, small gap, large gap,
# and both orderings of each so no test can pass by accident of argument order.
RATING_PAIRS = [
    (1200, 1200),
    (1200, 1400),
    (1400, 1200),
    (1000, 2000),
    (2000, 1000),
    (1500, 1520),
    (800, 801),
]


class TestExpectedScore:
    """The probability model itself."""

    def test_equal_ratings_expect_a_draw(self) -> None:
        assert expected_score(1200, 1200) == pytest.approx(0.5)

    def test_four_hundred_point_gap_is_ten_to_one(self) -> None:
        # The defining property of the 400 scale constant: a 400-point
        # favourite scores 10 times as often as the underdog.
        assert expected_score(1600, 1200) == pytest.approx(10 / 11)
        assert expected_score(1200, 1600) == pytest.approx(1 / 11)

    @pytest.mark.parametrize(("rating_a", "rating_b"), RATING_PAIRS)
    def test_complementary(self, rating_a: int, rating_b: int) -> None:
        # E(A) + E(B) == 1 is what makes rating zero-sum. If this drifts,
        # sum-preservation below is meaningless.
        assert expected_score(rating_a, rating_b) + expected_score(
            rating_b, rating_a
        ) == pytest.approx(1.0)

    def test_higher_rating_is_always_favoured(self) -> None:
        assert expected_score(1400, 1200) > 0.5
        assert expected_score(1200, 1400) < 0.5


class TestNewRating:
    """The update rule."""

    def test_performing_exactly_to_expectation_does_not_move_rating(self) -> None:
        assert new_rating(1200, expected=0.5, score=0.5) == 1200

    def test_returns_an_integer_the_column_can_store(self) -> None:
        # agents.battle_elo is INT: a float here would fail the insert at
        # runtime, not here, so assert the type at the boundary that decides it.
        assert isinstance(new_rating(1200, expected=0.37, score=1.0), int)


class TestUpsetRewards:
    """Beating a stronger opponent must pay more than beating a weaker one."""

    def test_beating_higher_rated_gains_more_than_beating_lower_rated(self) -> None:
        underdog_win = apply_battle_result(1200, 1600, Winner.A)
        favourite_win = apply_battle_result(1200, 800, Winner.A)

        assert underdog_win.a_delta > favourite_win.a_delta
        assert underdog_win.a_delta > 0
        assert favourite_win.a_delta > 0

    def test_losing_to_a_stronger_opponent_costs_less_than_losing_to_a_weaker_one(self) -> None:
        lost_to_favourite = apply_battle_result(1200, 1600, Winner.B)
        lost_to_underdog = apply_battle_result(1200, 800, Winner.B)

        # Both are losses, so both are negative; the upset defeat hurts more.
        assert lost_to_favourite.a_delta < 0
        assert lost_to_underdog.a_delta < 0
        assert lost_to_underdog.a_delta < lost_to_favourite.a_delta

    def test_gain_grows_monotonically_with_the_opponents_rating(self) -> None:
        gains = [
            apply_battle_result(1200, opponent, Winner.A).a_delta
            for opponent in range(800, 2001, 100)
        ]
        assert gains == sorted(gains), gains


class TestSumPreservation:
    """Rating is moved between fighters, never created."""

    @pytest.mark.parametrize(("rating_a", "rating_b"), RATING_PAIRS)
    @pytest.mark.parametrize("winner", [Winner.A, Winner.B, Winner.TIE])
    def test_total_rating_is_preserved(self, rating_a: int, rating_b: int, winner: Winner) -> None:
        change = apply_battle_result(rating_a, rating_b, winner)
        # Rounding is the only slack: two independent round() calls can each
        # move half a point, so the pair can differ by at most 1 in total.
        assert abs((change.a_after + change.b_after) - (rating_a + rating_b)) <= 1

    @pytest.mark.parametrize(("rating_a", "rating_b"), RATING_PAIRS)
    def test_the_winners_gain_is_the_losers_loss(self, rating_a: int, rating_b: int) -> None:
        change = apply_battle_result(rating_a, rating_b, Winner.A)
        assert change.a_delta == -change.b_delta

    def test_a_tie_between_unequal_ratings_moves_rating_toward_the_underdog(self) -> None:
        # A draw is a good result for the weaker side and a bad one for the
        # favourite — the sign here is the whole point of the Elo model.
        change = apply_battle_result(1200, 1600, Winner.TIE)
        assert change.a_delta > 0
        assert change.b_delta < 0

    def test_a_tie_between_equal_ratings_moves_nothing(self) -> None:
        change = apply_battle_result(1300, 1300, Winner.TIE)
        assert change.a_delta == 0
        assert change.b_delta == 0
        # Nothing moved, but the battle DID rate — distinct from the no-quorum
        # and self-play cases below, which report applied=False.
        assert change.applied is True


class TestKBounds:
    """K is the ceiling on a single battle's influence."""

    @pytest.mark.parametrize(("rating_a", "rating_b"), RATING_PAIRS)
    @pytest.mark.parametrize("winner", [Winner.A, Winner.B, Winner.TIE])
    def test_no_single_battle_moves_a_rating_further_than_k(
        self, rating_a: int, rating_b: int, winner: Winner
    ) -> None:
        change = apply_battle_result(rating_a, rating_b, winner)
        assert abs(change.a_delta) <= K_FACTOR
        assert abs(change.b_delta) <= K_FACTOR

    def test_the_maximum_swing_reaches_but_never_exceeds_k(self) -> None:
        # A 1000-point underdog winning is the extreme case: E(A) = 0.00315, so
        # the raw gain is 32 * 0.99685 = 31.899 — which ROUNDS to exactly K.
        # K is therefore attainable, not merely approached; the invariant is
        # `<= K`, and asserting `< K` here would be false.
        change = apply_battle_result(1000, 2000, Winner.A)
        assert change.a_delta == K_FACTOR
        assert change.a_delta <= K_FACTOR

    def test_a_smaller_k_bounds_the_swing_more_tightly(self) -> None:
        change = apply_battle_result(1000, 2000, Winner.A, k=8)
        assert abs(change.a_delta) <= 8


class TestNoRatingChangeCases:
    """The two outcomes that must move nothing — both anti-farming rules."""

    def test_no_quorum_leaves_both_ratings_untouched(self) -> None:
        change = apply_battle_result(1200, 1600, None)

        assert change.applied is False
        assert change.a_after == 1200
        assert change.b_after == 1600
        assert change.a_delta == 0
        assert change.b_delta == 0

    def test_same_owner_self_play_leaves_both_ratings_untouched(self) -> None:
        # Without this an owner farms rating against themselves for the price
        # of inference. The battle still completes; it just does not rate.
        change = apply_battle_result(1200, 1600, Winner.A, same_owner=True)

        assert change.applied is False
        assert change.a_delta == 0
        assert change.b_delta == 0

    @pytest.mark.parametrize("winner", [Winner.A, Winner.B, Winner.TIE, None])
    def test_same_owner_never_rates_whatever_the_verdict(self, winner: Winner | None) -> None:
        change = apply_battle_result(1200, 1600, winner, same_owner=True)
        assert change.applied is False
        assert (change.a_delta, change.b_delta) == (0, 0)

    def test_no_quorum_is_not_silently_treated_as_a_tie(self) -> None:
        # The failure this guards: mapping winner=None onto TIE would mint
        # tie-Elo out of a panel that never reached a verdict.
        no_quorum = apply_battle_result(1200, 1600, None)
        genuine_tie = apply_battle_result(1200, 1600, Winner.TIE)

        assert no_quorum.a_delta == 0
        assert genuine_tie.a_delta != 0
        assert no_quorum.a_delta != genuine_tie.a_delta


class TestFloorAndOverflow:
    """A rating stays a database-legal, non-overflowing integer at the extremes.

    Both properties guard the SAME downstream failure from different ends: a
    rating the persistence layer cannot store. V66's CHECK demands ``elo > 0``,
    so a rating that rounds to <= 0 makes settlement's UPDATE fail and strands
    the battle; and ``10.0 ** x`` raises past ~309, so an extreme gap crashes
    the expectation before any rating is computed at all.
    """

    def test_a_heavy_loss_from_a_near_floor_rating_never_drops_below_the_floor(self) -> None:
        # Two low-rated agents (equal, so E=0.5) — the loser's raw new rating is
        # round(10 + 32*(0 - 0.5)) = -6, which violates the elo>0 CHECK. The
        # floor is the only thing standing between that and a battle that can
        # never settle.
        assert new_rating(10, 0.5, 0.0) == ELO_FLOOR

    def test_the_floor_is_a_floor_not_a_reset(self) -> None:
        # A rating already above the floor is untouched — the clamp only ever
        # lifts a sub-floor value, never pins a healthy one.
        assert new_rating(1200, 0.5, 0.0) == round(1200 + K_FACTOR * (0.0 - 0.5))

    def test_apply_battle_result_floors_the_losers_rating(self) -> None:
        # The near-floor settlement case end to end: a low-rated agent taking a
        # loss stays >= floor rather than computing a CHECK-violating negative.
        change = apply_battle_result(ELO_FLOOR - 20, ELO_FLOOR - 20, Winner.B)
        assert change.a_after >= ELO_FLOOR
        assert change.b_after >= ELO_FLOOR

    @pytest.mark.parametrize(
        ("rating_a", "rating_b"),
        [(1, 10**9), (10**9, 1), (0, 10**12), (10**12, 0)],
    )
    def test_an_extreme_gap_does_not_overflow(self, rating_a: int, rating_b: int) -> None:
        # Without the exponent clamp, 10.0 ** ((rb-ra)/400) raises OverflowError
        # for a gap past ~123k. The result must still be a probability in [0, 1].
        score = expected_score(rating_a, rating_b)
        assert 0.0 <= score <= 1.0

    def test_an_extreme_gap_saturates_to_the_limit(self) -> None:
        # Clamping changes no representable result: the logistic has already
        # reached 1.0 / 0.0 to full float precision long before the clamp bites.
        assert expected_score(10**9, 1) == pytest.approx(1.0)
        assert expected_score(1, 10**9) == pytest.approx(0.0)

    def test_new_rating_clamps_at_the_ceiling(self) -> None:
        # A winner at the ceiling cannot grow past it — the INT column would
        # overflow and strand settlement from the top end, mirroring the floor.
        assert new_rating(ELO_CEILING, 0.5, 1.0) == ELO_CEILING


class TestFloorAndCeilingConservePoints:
    """The floor/ceiling must not mint or destroy rating (zero-sum holds).

    Clamping the two ratings INDEPENDENTLY mints points: two floor-rated agents,
    B wins, A stays at the floor (delta 0) while B still gains +16 — 16 points
    created from nothing. apply_battle_result clamps the pair TOGETHER, so the
    winner gains only what the loser actually loses after the clamp.
    """

    @pytest.mark.parametrize(("rating_a", "rating_b"), RATING_PAIRS)
    @pytest.mark.parametrize("winner", [Winner.A, Winner.B, Winner.TIE])
    def test_zero_sum_is_exact_everywhere(
        self, rating_a: int, rating_b: int, winner: Winner
    ) -> None:
        # Stronger than the <=1 rounding-slack property: with the single-delta
        # coupling the pair conserves EXACTLY, in bounds and at the bounds.
        change = apply_battle_result(rating_a, rating_b, winner)
        assert change.a_delta + change.b_delta == 0

    def test_two_floor_agents_conserve_when_one_loses(self) -> None:
        # THE finding-3 case. Independent flooring mints +16 for the winner;
        # coupling makes the winner gain only the loser's actual (zero) loss.
        change = apply_battle_result(ELO_FLOOR, ELO_FLOOR, Winner.B)
        assert change.a_after == ELO_FLOOR
        assert change.b_after == ELO_FLOOR
        assert change.a_delta + change.b_delta == 0

    def test_a_loss_that_partly_crosses_the_floor_conserves(self) -> None:
        # Loser 10 above the floor: a 16-point loss would cross it, so the loser
        # loses only 10 (clamped) and the winner gains exactly 10 — not 16.
        change = apply_battle_result(ELO_FLOOR + 10, ELO_FLOOR + 10, Winner.B)
        assert change.a_after == ELO_FLOOR
        assert change.a_delta == -10
        assert change.b_delta == 10
        assert change.a_delta + change.b_delta == 0

    def test_a_near_ceiling_winner_stays_storable_and_conserves(self) -> None:
        change = apply_battle_result(ELO_CEILING, ELO_CEILING, Winner.A)
        assert change.a_after <= ELO_CEILING
        assert change.b_after <= ELO_CEILING
        assert change.a_delta + change.b_delta == 0

    def test_both_below_floor_inputs_still_conserve(self) -> None:
        # BOTH ratings out of band on the SAME side (DB-legal as elo>0, e.g. a
        # legacy 80). It is impossible to floor both AND keep the raw sum, so the
        # inputs are clamped into band first — after which the pair conserves
        # exactly. Independent flooring (or handling only one clamped side) mints.
        change = apply_battle_result(80, 80, Winner.B)
        assert change.a_after >= ELO_FLOOR
        assert change.b_after >= ELO_FLOOR
        assert change.a_delta + change.b_delta == 0

    def test_both_above_ceiling_inputs_still_conserve(self) -> None:
        change = apply_battle_result(ELO_CEILING + 5000, ELO_CEILING + 5000, Winner.A)
        assert change.a_after <= ELO_CEILING
        assert change.b_after <= ELO_CEILING
        assert change.a_delta + change.b_delta == 0


class TestRatingChangeShape:
    """The record handed to the persistence layer."""

    def test_before_is_carried_so_the_battle_row_can_snapshot_it(self) -> None:
        change = apply_battle_result(DEFAULT_ELO, 1500, Winner.A)
        assert isinstance(change, RatingChange)
        assert change.a_before == DEFAULT_ELO
        assert change.b_before == 1500

    def test_is_immutable(self) -> None:
        change = apply_battle_result(1200, 1200, Winner.A)
        with pytest.raises(AttributeError):
            change.a_after = 9999  # type: ignore[misc]
