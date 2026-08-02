"""Pure move-quality derivation.

No database, no engine, no fixtures - every case here is built from literals,
which is the point: these are the rules that decide whether a user is told they
blundered, and they must be checkable exhaustively and instantly.
"""

import pytest

from app.services.move_quality import (
    ALLOWED_MATE,
    AMBIGUOUS_MATE,
    BLUNDER,
    CP,
    CRITICAL,
    GOOD,
    INACCURACY,
    MATE_AGAINST,
    MATE_FOR,
    MATE_SEQUENCE,
    MISSED_MATE,
    MISSING_AFTER_EVALUATION,
    MISSING_BEFORE_EVALUATION,
    MISSING_MOVE_DATA,
    MISTAKE,
    NON_CONTIGUOUS_EVALUATIONS,
    UNSCORED,
    MoveQuality,
    PlayedMove,
    StoredScore,
    classify_move,
    derive_move_quality,
    normalize_score,
    normalize_uci,
    quality_for_loss,
    side_for_ply,
)

# --- builders -------------------------------------------------------------


def cp(ply: int, score_white: int, best: str | None = None) -> StoredScore:
    return StoredScore(
        ply=ply, score_cp_white=score_white, mate_in_white=None, best_move_uci=best
    )


def mate(ply: int, mate_white: int, best: str | None = None) -> StoredScore:
    return StoredScore(
        ply=ply, score_cp_white=None, mate_in_white=mate_white, best_move_uci=best
    )


def played(ply: int = 1, san: str = "Nf3", uci: str = "g1f3") -> PlayedMove:
    return PlayedMove(ply=ply, san=san, uci=uci)


def grade_white(before: StoredScore, after: StoredScore, **kwargs) -> MoveQuality:
    """Grade White's first move, ply 1, from evaluations at plies 0 and 1."""
    return classify_move(played(ply=1, **kwargs), before, after, player_is_white=True)


def grade_black(before: StoredScore, after: StoredScore, **kwargs) -> MoveQuality:
    """Grade Black's first move, ply 2, from evaluations at plies 1 and 2."""
    return classify_move(played(ply=2, **kwargs), before, after, player_is_white=False)


# --- perspective ----------------------------------------------------------


def test_white_player_loses_centipawns_when_the_white_score_drops():
    result = grade_white(cp(0, 60), cp(1, 10))

    assert result.centipawn_loss == 50
    assert result.quality == INACCURACY
    assert result.side == "white"


def test_black_player_loses_centipawns_when_the_white_score_rises():
    """The same swing, stored identically, must grade Black rather than White.

    White's score improving by 50 is Black losing 50. Getting this backwards
    would tell a Black player their worst moves were their best ones.
    """
    result = grade_black(cp(1, -60), cp(2, -10))

    assert result.centipawn_loss == 50
    assert result.quality == INACCURACY
    assert result.side == "black"


def test_the_same_stored_pair_grades_oppositely_for_the_two_colours():
    before, after = cp(1, -60), cp(2, -10)

    black = classify_move(played(ply=2), before, after, player_is_white=False)
    white = classify_move(played(ply=2), before, after, player_is_white=True)

    assert black.centipawn_loss == 50
    assert white.centipawn_loss == 0


def test_score_before_and_after_are_reported_from_the_player_perspective():
    result = grade_black(cp(1, -300), cp(2, -250))

    assert result.score_before.kind == CP
    assert result.score_before.cp == 300
    assert result.score_after.cp == 250


# --- centipawn loss -------------------------------------------------------


def test_holding_the_evaluation_loses_nothing():
    assert grade_white(cp(0, 35), cp(1, 35)).centipawn_loss == 0


def test_improving_the_evaluation_is_clamped_to_zero_not_negative():
    result = grade_white(cp(0, 20), cp(1, 400))

    assert result.centipawn_loss == 0
    assert result.quality == GOOD


def test_centipawn_loss_is_an_integer():
    assert isinstance(grade_white(cp(0, 51), cp(1, 0)).centipawn_loss, int)


@pytest.mark.parametrize(
    ("loss", "expected"),
    [
        (0, GOOD),
        (49, GOOD),
        (50, INACCURACY),
        (99, INACCURACY),
        (100, MISTAKE),
        (199, MISTAKE),
        (200, BLUNDER),
        (5000, BLUNDER),
    ],
)
def test_threshold_boundaries(loss: int, expected: str):
    assert quality_for_loss(loss) == expected


@pytest.mark.parametrize(
    ("loss", "expected"),
    [(49, GOOD), (50, INACCURACY), (99, INACCURACY), (100, MISTAKE), (199, MISTAKE), (200, BLUNDER)],
)
def test_threshold_boundaries_end_to_end(loss: int, expected: str):
    """The same boundaries through the real derivation, not just the labeller."""
    result = grade_white(cp(0, loss), cp(1, 0))

    assert result.centipawn_loss == loss
    assert result.quality == expected


# --- best move ------------------------------------------------------------


def test_playing_the_engine_move_is_an_exact_match():
    result = grade_white(cp(0, 30, best="g1f3"), cp(1, 30))

    assert result.is_exact_best_move is True


def test_a_different_move_with_low_loss_is_still_good():
    """Several moves can be similarly strong. Not matching the engine's single
    stored best move is evidence, never a grade."""
    result = grade_white(cp(0, 30, best="e2e4"), cp(1, 20))

    assert result.is_exact_best_move is False
    assert result.quality == GOOD


def test_a_missing_best_move_is_null_not_false():
    result = grade_white(cp(0, 30, best=None), cp(1, 30))

    assert result.is_exact_best_move is None


def test_an_empty_best_move_string_is_treated_as_missing():
    result = grade_white(cp(0, 30, best="  "), cp(1, 30))

    assert result.is_exact_best_move is None


def test_the_best_move_comes_from_the_position_the_player_faced():
    """It lives on the ply k-1 evaluation. Reading it from the after-position
    would compare the played move against the *reply* to it."""
    result = grade_white(cp(0, 30, best="g1f3"), cp(1, 30, best="e7e5"))

    assert result.best_move_uci == "g1f3"
    assert result.is_exact_best_move is True


def test_uci_comparison_is_case_and_whitespace_insensitive():
    result = grade_white(cp(0, 30, best=" G1F3 "), cp(1, 30))

    assert result.is_exact_best_move is True


def test_promotion_suffix_is_part_of_the_comparison():
    queen = classify_move(
        played(ply=1, san="e8=Q", uci="e7e8q"), cp(0, 30, best="e7e8q"), cp(1, 30),
        player_is_white=True,
    )
    knight = classify_move(
        played(ply=1, san="e8=Q", uci="e7e8q"), cp(0, 30, best="e7e8n"), cp(1, 30),
        player_is_white=True,
    )

    assert queen.is_exact_best_move is True
    assert knight.is_exact_best_move is False


def test_a_promotion_does_not_match_the_same_move_without_a_suffix():
    result = classify_move(
        played(ply=1, san="e8=Q", uci="e7e8q"), cp(0, 30, best="e7e8"), cp(1, 30),
        player_is_white=True,
    )

    assert result.is_exact_best_move is False


@pytest.mark.parametrize(("raw", "expected"), [("G1F3", "g1f3"), (" e2e4 ", "e2e4"), ("", None), (None, None)])
def test_normalize_uci(raw, expected):
    assert normalize_uci(raw) == expected


# --- unscored -------------------------------------------------------------


def test_a_missing_before_evaluation_is_unscored():
    result = classify_move(played(ply=1), None, cp(1, 30), player_is_white=True)

    assert result.quality == UNSCORED
    assert result.unscored_reason == MISSING_BEFORE_EVALUATION
    assert result.centipawn_loss is None


def test_a_missing_after_evaluation_is_unscored():
    result = classify_move(played(ply=1), cp(0, 30), None, player_is_white=True)

    assert result.unscored_reason == MISSING_AFTER_EVALUATION


def test_evaluations_for_the_wrong_plies_are_unscored_not_graded():
    """Pairing a move with someone else's positions would produce a confident,
    wrong number - so it is refused rather than computed."""
    result = classify_move(played(ply=5), cp(2, 30), cp(3, 500), player_is_white=True)

    assert result.unscored_reason == NON_CONTIGUOUS_EVALUATIONS
    assert result.centipawn_loss is None


def test_a_move_with_no_uci_is_unscored():
    result = classify_move(
        PlayedMove(ply=1, san="Nf3", uci=""), cp(0, 30), cp(1, 30), player_is_white=True
    )

    assert result.unscored_reason == MISSING_MOVE_DATA


def test_an_unattributable_mate_zero_is_unscored_rather_than_guessed():
    """A mate 0 needs ply parity to know who delivered it, and ply 0 is the
    starting position - so there is no honest answer."""
    result = classify_move(played(ply=1), mate(0, 0), cp(1, 30), player_is_white=True)

    assert result.unscored_reason == AMBIGUOUS_MATE
    assert result.centipawn_loss is None


def test_an_evaluation_with_neither_score_is_a_loud_failure():
    with pytest.raises(ValueError):
        normalize_score(None, None, 4, player_is_white=True)


# --- mate 0 and ply parity ------------------------------------------------


def test_mate_zero_at_an_odd_ply_means_white_delivered_mate():
    score = normalize_score(None, 0, 7, player_is_white=True)

    assert score.kind == MATE_FOR
    assert score.mate_distance == 0


def test_mate_zero_at_an_even_ply_means_black_delivered_mate():
    score = normalize_score(None, 0, 4, player_is_white=False)

    assert score.kind == MATE_FOR
    assert score.mate_distance == 0


def test_mate_zero_at_an_odd_ply_is_a_loss_for_the_black_player():
    assert normalize_score(None, 0, 7, player_is_white=False).kind == MATE_AGAINST


def test_mate_zero_at_an_even_ply_is_a_loss_for_the_white_player():
    assert normalize_score(None, 0, 4, player_is_white=True).kind == MATE_AGAINST


def test_white_delivering_checkmate_is_never_a_missed_mate():
    """The regression this whole parity rule exists for.

    White has mate in 1 and plays it. The final position stores
    mate_in_white = 0, which is neither positive nor negative - so a sign test
    reads "no longer has a mate" and reports the winning move as a blunder.
    """
    result = classify_move(played(ply=7), mate(6, 1), mate(7, 0), player_is_white=True)

    assert result.special_classification != MISSED_MATE
    assert result.quality != CRITICAL
    assert result.score_after.kind == MATE_FOR


def test_black_delivering_checkmate_is_never_a_missed_mate():
    """Same case for Black, where the perspective flip cannot help either:
    -1 * 0 is still 0."""
    result = classify_move(played(ply=4), mate(3, -1), mate(4, 0), player_is_white=False)

    assert result.special_classification != MISSED_MATE
    assert result.quality != CRITICAL
    assert result.score_after.kind == MATE_FOR


def test_being_checkmated_is_recorded_against_the_player_who_was_mated():
    """White's move at ply 7 walks into a mate that Black delivers at ply 8."""
    result = classify_move(played(ply=7), cp(6, 40), mate(7, -1), player_is_white=True)

    assert result.special_classification == ALLOWED_MATE


# --- the transition table -------------------------------------------------


def _score_of_kind(kind: str, ply: int, *, player_is_white: bool) -> StoredScore:
    """A stored row that normalizes to `kind` for the given player."""
    if kind == CP:
        return cp(ply, 100 if player_is_white else -100)
    mater_is_white = player_is_white if kind == MATE_FOR else not player_is_white
    return mate(ply, 3 if mater_is_white else -3)


# ADR 0002 section D4, all nine cells.
TRANSITIONS = [
    (CP, CP, GOOD, None),
    (CP, MATE_FOR, GOOD, None),
    (CP, MATE_AGAINST, CRITICAL, ALLOWED_MATE),
    (MATE_FOR, CP, CRITICAL, MISSED_MATE),
    (MATE_FOR, MATE_FOR, UNSCORED, MATE_SEQUENCE),
    (MATE_FOR, MATE_AGAINST, CRITICAL, ALLOWED_MATE),
    (MATE_AGAINST, CP, GOOD, None),
    (MATE_AGAINST, MATE_FOR, GOOD, None),
    (MATE_AGAINST, MATE_AGAINST, UNSCORED, MATE_SEQUENCE),
]


@pytest.mark.parametrize(("before_kind", "after_kind", "quality", "special"), TRANSITIONS)
@pytest.mark.parametrize("player_is_white", [True, False])
def test_every_transition_cell(before_kind, after_kind, quality, special, player_is_white):
    move_ply = 1 if player_is_white else 2
    result = classify_move(
        played(ply=move_ply),
        _score_of_kind(before_kind, move_ply - 1, player_is_white=player_is_white),
        _score_of_kind(after_kind, move_ply, player_is_white=player_is_white),
        player_is_white=player_is_white,
    )

    assert result.quality == quality
    assert result.special_classification == special


@pytest.mark.parametrize(("before_kind", "after_kind", "quality", "special"), TRANSITIONS)
def test_no_transition_involving_mate_produces_a_centipawn_number(
    before_kind, after_kind, quality, special
):
    """A mate is never converted into a score, however convenient that would be
    for averaging."""
    result = classify_move(
        played(ply=1),
        _score_of_kind(before_kind, 0, player_is_white=True),
        _score_of_kind(after_kind, 1, player_is_white=True),
        player_is_white=True,
    )

    if before_kind == CP and after_kind == CP:
        assert result.centipawn_loss is not None
    else:
        assert result.centipawn_loss is None


def test_a_shorter_mate_is_not_a_centipawn_gain_and_a_longer_one_is_not_a_loss():
    faster = classify_move(played(ply=1), mate(0, 5), mate(1, 2), player_is_white=True)
    slower = classify_move(played(ply=1), mate(0, 2), mate(1, 5), player_is_white=True)

    assert faster.centipawn_loss is None and slower.centipawn_loss is None
    assert faster.special_classification == MATE_SEQUENCE
    assert slower.special_classification == MATE_SEQUENCE
    assert not faster.is_critical and not slower.is_critical


def test_mate_sequence_reports_a_reason_as_well_as_a_classification():
    result = classify_move(played(ply=1), mate(0, 4), mate(1, 3), player_is_white=True)

    assert result.unscored_reason == MATE_SEQUENCE
    assert result.is_graded is False


def test_mate_errors_are_critical_but_not_blunders():
    """Critical totals add mate errors to blunders. If a mate error were also
    labelled a blunder it would be counted twice."""
    missed = classify_move(played(ply=1), mate(0, 2), cp(1, 50), player_is_white=True)
    allowed = classify_move(played(ply=1), cp(0, 50), mate(1, -2), player_is_white=True)

    for result in (missed, allowed):
        assert result.quality == CRITICAL
        assert result.quality != BLUNDER
        assert result.is_critical
        assert result.is_graded


def test_escaping_a_forced_mate_is_not_a_mistake():
    result = classify_move(played(ply=1), mate(0, -3), cp(1, -120), player_is_white=True)

    assert result.quality == GOOD
    assert result.special_classification is None


def test_finding_a_forced_mate_is_not_a_mistake():
    result = classify_move(played(ply=1), cp(0, 250), mate(1, 3), player_is_white=True)

    assert result.quality == GOOD
    assert result.centipawn_loss is None


# --- move identity --------------------------------------------------------


@pytest.mark.parametrize(("ply", "expected"), [(1, "white"), (2, "black"), (7, "white"), (88, "black")])
def test_side_for_ply(ply: int, expected: str):
    assert side_for_ply(ply) == expected


@pytest.mark.parametrize(("ply", "expected"), [(1, 1), (2, 1), (3, 2), (4, 2), (89, 45)])
def test_move_number_for_ply(ply: int, expected: int):
    from app.services.move_quality import move_number_for_ply

    assert move_number_for_ply(ply) == expected


# --- whole-game derivation ------------------------------------------------


def _game(moves: int = 6) -> tuple[list[PlayedMove], list[StoredScore]]:
    played_moves = [played(ply=ply, san=f"m{ply}", uci=f"a1a{ply}") for ply in range(1, moves + 1)]
    evaluations = [cp(ply, 0, best=f"a1a{ply + 1}") for ply in range(0, moves + 1)]
    return played_moves, evaluations


def test_only_the_selected_players_moves_are_graded():
    moves, evaluations = _game(moves=6)

    white = derive_move_quality(moves, evaluations, "white")
    black = derive_move_quality(moves, evaluations, "black")

    assert [m.ply for m in white] == [1, 3, 5]
    assert [m.ply for m in black] == [2, 4, 6]


def test_results_are_returned_in_ply_order_whatever_the_input_order():
    moves, evaluations = _game(moves=6)

    result = derive_move_quality(list(reversed(moves)), evaluations, "white")

    assert [m.ply for m in result] == [1, 3, 5]


def test_an_unknown_player_colour_is_refused_rather_than_defaulted_to_white():
    """`user_color` is "unknown" whenever the saved player matched neither side.
    Defaulting would report an opponent's moves as the user's own."""
    moves, evaluations = _game()

    with pytest.raises(ValueError):
        derive_move_quality(moves, evaluations, "unknown")


def test_a_game_missing_its_final_evaluation_grades_the_rest():
    moves, evaluations = _game(moves=4)

    result = derive_move_quality(moves, evaluations[:-1], "black")

    assert result[0].quality == GOOD
    assert result[-1].ply == 4
    assert result[-1].unscored_reason == MISSING_AFTER_EVALUATION


def test_derivation_imports_nothing_beyond_the_standard_library():
    """Guards the module's purity by its imports rather than its prose.

    CP2 and CP3 depend on these rules being testable without a database or a
    Stockfish binary. An import of sqlalchemy, python-chess, or app.core would
    quietly end that, so it fails here instead.
    """
    import ast

    import app.services.move_quality as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= {"dataclasses", "typing"}, f"unexpected imports: {imported}"
