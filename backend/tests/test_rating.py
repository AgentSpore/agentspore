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
