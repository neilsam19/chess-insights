"""Per-game aggregation of move-quality facts.

Facts are constructed directly rather than derived, so a failure here points at
the aggregation and never at the classification rules tested in
`test_move_quality.py`.
"""

import pytest

from app.services.move_quality import (
    ALLOWED_MATE,
    BLUNDER,
    CRITICAL,
    GOOD,
    INACCURACY,
    MATE_SEQUENCE,
    MISSED_MATE,
    MISTAKE,
    UNSCORED,
    MoveQuality,
    summarize_moves,
)

_PLY = iter(range(1, 10_000, 2))


def fact(
    quality: str = GOOD,
    *,
    centipawn_loss: int | None = None,
    special: str | None = None,
    is_exact_best_move: bool | None = None,
) -> MoveQuality:
    ply = next(_PLY)
    return MoveQuality(
        ply=ply,
        move_number=(ply + 1) // 2,
        side="white",
        san="Nf3",
        played_move_uci="g1f3",
        best_move_uci="g1f3" if is_exact_best_move is not None else None,
        is_exact_best_move=is_exact_best_move,
        score_before=None,
        score_after=None,
        centipawn_loss=centipawn_loss,
        quality=quality,
        special_classification=special,
        unscored_reason=MATE_SEQUENCE if special == MATE_SEQUENCE else None,
    )


def graded(loss: int, quality: str) -> MoveQuality:
    return fact(quality, centipawn_loss=loss)


def missed_mate() -> MoveQuality:
    return fact(CRITICAL, special=MISSED_MATE)


def allowed_mate() -> MoveQuality:
    return fact(CRITICAL, special=ALLOWED_MATE)


def mate_sequence() -> MoveQuality:
    return fact(UNSCORED, special=MATE_SEQUENCE)


# --- averages -------------------------------------------------------------


def test_average_and_median_use_the_numeric_moves_only():
    summary = summarize_moves([graded(0, GOOD), graded(100, MISTAKE), graded(200, BLUNDER)])

    assert summary.average_centipawn_loss == 100
    assert summary.median_centipawn_loss == 100
    assert summary.numeric_cpl_moves == 3


def test_mate_events_are_excluded_from_the_centipawn_average():
    """A missed mate has no centipawn value. Including it would require
    inventing one, which is the thing this sprint refuses to do."""
    without = summarize_moves([graded(40, GOOD), graded(60, INACCURACY)])
    with_mates = summarize_moves(
        [graded(40, GOOD), graded(60, INACCURACY), missed_mate(), allowed_mate()]
    )

    assert with_mates.average_centipawn_loss == without.average_centipawn_loss == 50
    assert with_mates.numeric_cpl_moves == 2


def test_the_numeric_sample_size_is_reported_next_to_the_average():
    """An ACPL over two moves and an ACPL over sixty are not the same claim."""
    summary = summarize_moves([graded(30, GOOD), missed_mate(), mate_sequence()])

    assert summary.total_player_moves == 3
    assert summary.numeric_cpl_moves == 1


def test_median_with_an_odd_sample():
    summary = summarize_moves([graded(10, GOOD), graded(300, BLUNDER), graded(20, GOOD)])

    assert summary.median_centipawn_loss == 20


def test_median_with_an_even_sample_averages_the_middle_pair():
    summary = summarize_moves(
        [graded(10, GOOD), graded(20, GOOD), graded(40, GOOD), graded(400, BLUNDER)]
    )

    assert summary.median_centipawn_loss == 30


def test_median_is_not_the_mean():
    """One catastrophe should move the average far more than the median."""
    summary = summarize_moves(
        [graded(0, GOOD), graded(10, GOOD), graded(10, GOOD), graded(20, GOOD), graded(900, BLUNDER)]
    )

    assert summary.median_centipawn_loss == 10
    assert summary.average_centipawn_loss == 188


def test_no_numeric_moves_reports_null_not_zero():
    """"ACPL 0" reads as flawless play. The honest answer is "not measured"."""
    summary = summarize_moves([missed_mate(), mate_sequence()])

    assert summary.average_centipawn_loss is None
    assert summary.median_centipawn_loss is None


def test_an_empty_game_is_all_zeros_and_nulls():
    summary = summarize_moves([])

    assert summary.total_player_moves == 0
    assert summary.graded_moves == 0
    assert summary.coverage_percent == 0
    assert summary.average_centipawn_loss is None
    assert summary.exact_best_move_percent is None


# --- coverage -------------------------------------------------------------


def test_coverage_counts_graded_moves_over_all_player_moves():
    summary = summarize_moves([graded(10, GOOD), graded(10, GOOD), graded(10, GOOD), mate_sequence()])

    assert summary.graded_moves == 3
    assert summary.unscored_moves == 1
    assert summary.coverage_percent == 75


def test_mate_errors_count_as_graded_coverage():
    """They are measured facts, not gaps. Treating them as ungraded would let a
    game full of missed mates report high coverage and no mistakes."""
    summary = summarize_moves([missed_mate(), allowed_mate()])

    assert summary.graded_moves == 2
    assert summary.unscored_moves == 0
    assert summary.coverage_percent == 100


def test_a_mate_sequence_is_neither_graded_nor_critical():
    summary = summarize_moves([mate_sequence(), mate_sequence()])

    assert summary.mate_sequences == 2
    assert summary.graded_moves == 0
    assert summary.critical_mistakes == 0


def test_a_percentage_on_a_half_rounds_away_from_zero():
    """1 of 8 is 12.5%, which must display as 13 rather than 12.

    Python's built-in round() is half-to-even, so it would round 12.5 down to
    12 while rounding 37.5 up to 38 - two identical halves going in opposite
    directions. The module picks half-away-from-zero deliberately; without this
    test both percentage helpers can be swapped to round() and the whole suite
    stays green.
    """
    summary = summarize_moves([graded(100, MISTAKE)] + [fact(UNSCORED) for _ in range(7)])

    assert summary.graded_moves == 1
    assert summary.total_player_moves == 8
    assert summary.coverage_percent == 13


def test_the_engine_agreement_rate_rounds_the_same_way():
    facts = [fact(is_exact_best_move=index == 0) for index in range(8)]

    summary = summarize_moves(facts)

    assert summary.exact_best_move_count == 1
    assert summary.exact_best_move_comparable == 8
    assert summary.exact_best_move_percent == 13


# --- mistake counts -------------------------------------------------------


def test_each_quality_label_is_counted_separately():
    summary = summarize_moves(
        [
            graded(0, GOOD),
            graded(60, INACCURACY),
            graded(70, INACCURACY),
            graded(150, MISTAKE),
            graded(500, BLUNDER),
        ]
    )

    assert (summary.inaccuracies, summary.mistakes, summary.blunders) == (2, 1, 1)


def test_critical_mistakes_are_blunders_plus_mate_errors():
    summary = summarize_moves(
        [graded(500, BLUNDER), graded(600, BLUNDER), missed_mate(), allowed_mate(), allowed_mate()]
    )

    assert summary.blunders == 2
    assert summary.missed_mates == 1
    assert summary.allowed_mates == 2
    assert summary.critical_mistakes == 5


def test_a_mate_error_is_not_also_counted_as_a_blunder():
    """The two labels are disjoint precisely so this total cannot double-count."""
    summary = summarize_moves([missed_mate(), allowed_mate()])

    assert summary.blunders == 0
    assert summary.critical_mistakes == 2


def test_good_moves_are_not_counted_as_mistakes():
    summary = summarize_moves([graded(0, GOOD), graded(49, GOOD)])

    assert summary.critical_mistakes == 0
    assert summary.inaccuracies == 0


# --- best-move rate -------------------------------------------------------


def test_the_best_move_rate_excludes_moves_with_nothing_to_compare():
    """Four moves, three comparable, two matched - the rate is 2 of 3."""
    summary = summarize_moves(
        [
            fact(GOOD, centipawn_loss=0, is_exact_best_move=True),
            fact(GOOD, centipawn_loss=0, is_exact_best_move=True),
            fact(GOOD, centipawn_loss=20, is_exact_best_move=False),
            fact(GOOD, centipawn_loss=10, is_exact_best_move=None),
        ]
    )

    assert summary.exact_best_move_count == 2
    assert summary.exact_best_move_comparable == 3
    assert summary.exact_best_move_percent == 67


def test_the_best_move_rate_is_null_when_nothing_is_comparable():
    summary = summarize_moves([fact(GOOD, centipawn_loss=0, is_exact_best_move=None)])

    assert summary.exact_best_move_percent is None


def test_matching_the_engine_is_not_required_to_avoid_mistakes():
    """Every move here differs from the engine's choice and every one is good.
    The summary must not imply otherwise."""
    summary = summarize_moves(
        [fact(GOOD, centipawn_loss=loss, is_exact_best_move=False) for loss in (0, 10, 40)]
    )

    assert summary.exact_best_move_percent == 0
    assert summary.inaccuracies == summary.mistakes == summary.blunders == 0
    assert summary.average_centipawn_loss == 17


@pytest.mark.parametrize(
    ("losses", "expected"),
    [([0], 0), ([1], 1), ([1, 2], 2), ([10, 11], 11), ([2, 3], 3)],
)
def test_rounding_is_half_away_from_zero_not_half_to_even(losses, expected):
    """Python's round() would send 2.5 to 2 and 10.5 to 11. Displaying both
    next to each other looks like a bug even though it is defensible."""
    assert summarize_moves([graded(loss, GOOD) for loss in losses]).average_centipawn_loss == expected
