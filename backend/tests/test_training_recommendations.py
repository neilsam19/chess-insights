"""The rules that decide what to work on.

No database and no engine: every rule is a function over response objects, so
each evidence floor can be pushed at from both sides. That matters more here
than anywhere else in the sprint, because a recommendation is the one output a
reader will act on without checking - so the tests assert both that a rule
fires when the evidence is there and that it stays silent when it is not.
"""

import re
import uuid

import pytest

from app.schemas.move_quality import PlayerScoreOut
from app.schemas.upload_move_quality import (
    ColorMoveQualityOut,
    MoveQualityMetricsOut,
    OpeningMoveQualityOut,
    ReviewMoveOut,
    UploadCoverageOut,
)
from app.services.training_recommendations import (
    COLOR_MINIMUM_GAMES,
    COLOR_MINIMUM_GRADED_MOVES,
    COLOR_PRIORITY,
    CONVERSION_PRACTICE,
    EVIDENCE_LIMIT,
    OPENING_MINIMUM_GAMES,
    OPENING_MINIMUM_GRADED_MOVES,
    OPENING_PRIORITY,
    RECOMMENDATION_ORDER,
    REVIEW_MISTAKES,
    build_recommendations,
)

GAME_ID = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")


def metrics(
    *,
    games: int = 5,
    graded_moves: int = 200,
    average_centipawn_loss: int | None = 20,
    critical_mistakes: int = 2,
) -> MoveQualityMetricsOut:
    return MoveQualityMetricsOut(
        games=games,
        total_player_moves=graded_moves,
        graded_moves=graded_moves,
        numeric_cpl_moves=graded_moves,
        unscored_moves=0,
        coverage_percent=100,
        average_centipawn_loss=average_centipawn_loss,
        median_centipawn_loss=average_centipawn_loss,
        exact_best_move_count=0,
        exact_best_move_comparable=graded_moves,
        exact_best_move_percent=0,
        inaccuracies=0,
        mistakes=0,
        blunders=critical_mistakes,
        missed_mates=0,
        allowed_mates=0,
        mate_sequences=0,
        critical_mistakes=critical_mistakes,
    )


def coverage(*, total: int = 10, complete: int = 10, not_gradable: int = 0) -> UploadCoverageOut:
    return UploadCoverageOut(
        total_games=total,
        complete_games=complete,
        queued_games=0,
        running_games=0,
        failed_games=0,
        cancelled_games=total - complete - not_gradable,
        not_analyzed_games=0,
        not_gradable_games=not_gradable,
        graded_player_moves=400,
        numeric_cpl_moves=395,
    )


def cp(value: int) -> PlayerScoreOut:
    return PlayerScoreOut(kind="cp", cp=value, mate_distance=None)


def review_move(
    *,
    ply: int = 21,
    game_index: int = 0,
    loss: int | None = 300,
    before: PlayerScoreOut | None = None,
    opening: str | None = "Bishop's Opening",
    color: str = "white",
    special: str | None = None,
    quality: str = "blunder",
) -> ReviewMoveOut:
    return ReviewMoveOut(
        game_id=GAME_ID,
        game_index=game_index,
        ply=ply,
        move_number=(ply + 1) // 2,
        side="white" if ply % 2 else "black",
        san="Qh5",
        player_color=color,
        opening=opening,
        eco="C23",
        best_move_uci="g1f3",
        score_before=cp(0) if before is None else before,
        score_after=cp(-300),
        centipawn_loss=loss,
        quality=quality,
        special_classification=special,
    )


def build(**overrides):
    kwargs = {
        "coverage": coverage(),
        "overall": metrics(),
        "by_color": [],
        "by_opening": [],
        "review_moves": [],
    }
    kwargs.update(overrides)
    return build_recommendations(**kwargs)


def of_type(recommendations, name):
    return next((r for r in recommendations if r.type == name), None)


# --- nothing to say --------------------------------------------------------


class TestSilence:
    def test_an_empty_library_produces_nothing(self):
        assert build() == []

    def test_never_invents_a_recommendation_to_fill_the_space(self):
        """One mistake supports reviewing one mistake, and nothing else.

        The whole point of the evidence floors: a thin library gets fewer
        recommendations rather than the same number of weaker ones.
        """
        result = build(review_moves=[review_move()])

        assert [r.type for r in result] == [REVIEW_MISTAKES]


# --- review the largest mistakes ------------------------------------------


class TestReviewMistakes:
    def test_carries_at_most_five_positions_in_the_given_order(self):
        moves = [review_move(ply=ply, loss=500 - ply) for ply in range(1, 15, 2)]

        recommendation = of_type(build(review_moves=moves), REVIEW_MISTAKES)

        assert len(recommendation.evidence_items) == EVIDENCE_LIMIT
        assert recommendation.evidence_items == moves[:EVIDENCE_LIMIT]

    def test_states_the_range_it_is_based_on(self):
        moves = [review_move(ply=1, loss=500), review_move(ply=3, loss=210)]

        recommendation = of_type(build(review_moves=moves), REVIEW_MISTAKES)

        assert "5.00" in recommendation.evidence_summary
        assert "2.10" in recommendation.evidence_summary

    def test_describes_a_mate_error_as_a_mate_error_not_a_pawn_count(self):
        moves = [review_move(loss=None, special="allowed_mate", quality="critical")]

        recommendation = of_type(build(review_moves=moves), REVIEW_MISTAKES)

        assert "forced mate" in recommendation.evidence_summary
        assert not re.search(r"\d+\.\d\d pawns", recommendation.evidence_summary)


# --- conversion ------------------------------------------------------------


class TestConversionPractice:
    def _moves(self, winning: int, total: int):
        return [
            review_move(ply=1 + 2 * index, before=cp(300 if index < winning else -50))
            for index in range(total)
        ]

    def test_fires_when_most_costly_moves_came_from_a_winning_position(self):
        recommendation = of_type(build(review_moves=self._moves(3, 5)), CONVERSION_PRACTICE)

        assert recommendation is not None
        assert "60%" in recommendation.explanation
        assert len(recommendation.evidence_items) == 3

    def test_stays_silent_when_the_mistakes_were_made_from_worse_positions(self):
        assert of_type(build(review_moves=self._moves(2, 5)), CONVERSION_PRACTICE) is None

    def test_stays_silent_below_the_sample_floor(self):
        """Four moves cannot establish a pattern however they are distributed."""
        assert of_type(build(review_moves=self._moves(4, 4)), CONVERSION_PRACTICE) is None

    def test_a_forced_mate_in_hand_counts_as_winning(self):
        moves = [
            review_move(
                ply=1 + 2 * index,
                before=PlayerScoreOut(kind="mate_for", cp=None, mate_distance=3),
            )
            for index in range(5)
        ]

        recommendation = of_type(build(review_moves=moves), CONVERSION_PRACTICE)

        assert recommendation is not None
        assert "100%" in recommendation.explanation

    def test_being_mated_is_never_counted_as_winning(self):
        moves = [
            review_move(
                ply=1 + 2 * index,
                before=PlayerScoreOut(kind="mate_against", cp=None, mate_distance=2),
            )
            for index in range(5)
        ]

        assert of_type(build(review_moves=moves), CONVERSION_PRACTICE) is None

    def test_a_move_with_no_recorded_score_is_not_guessed_at(self):
        moves = [review_move(ply=1 + 2 * i, before=cp(300)) for i in range(4)]
        moves += [review_move(ply=99)]
        moves[-1] = moves[-1].model_copy(update={"score_before": None})

        # Four known scores, all winning - still under the floor, because the
        # unknown one is excluded rather than assumed either way.
        assert of_type(build(review_moves=moves), CONVERSION_PRACTICE) is None


# --- opening priority ------------------------------------------------------


class TestOpeningPriority:
    def opening(self, name, *, games, graded, acpl, critical=2):
        return OpeningMoveQualityOut(
            opening=name,
            eco="B90",
            metrics=metrics(
                games=games,
                graded_moves=graded,
                average_centipawn_loss=acpl,
                critical_mistakes=critical,
            ),
        )

    def test_one_game_is_never_enough(self):
        buckets = [self.opening("Sicilian Defense", games=1, graded=400, acpl=90)]

        assert of_type(build(by_opening=buckets), OPENING_PRIORITY) is None

    def test_two_games_of_barely_any_moves_are_never_enough(self):
        buckets = [
            self.opening(
                "Sicilian Defense",
                games=OPENING_MINIMUM_GAMES,
                graded=OPENING_MINIMUM_GRADED_MOVES - 1,
                acpl=90,
            )
        ]

        assert of_type(build(by_opening=buckets), OPENING_PRIORITY) is None

    def test_fires_exactly_at_both_floors(self):
        buckets = [
            self.opening(
                "Sicilian Defense",
                games=OPENING_MINIMUM_GAMES,
                graded=OPENING_MINIMUM_GRADED_MOVES,
                acpl=90,
            )
        ]

        recommendation = of_type(build(by_opening=buckets), OPENING_PRIORITY)

        assert recommendation is not None
        assert "Sicilian Defense" in recommendation.title

    def test_selects_the_weakest_opening_that_qualifies(self):
        buckets = [
            self.opening("Well Played Opening", games=6, graded=300, acpl=15),
            self.opening("Costly Opening", games=3, graded=150, acpl=70),
            # Worse than either, and correctly ignored - one game.
            self.opening("Barely Played Opening", games=1, graded=60, acpl=200),
        ]

        recommendation = of_type(build(by_opening=buckets), OPENING_PRIORITY)

        assert "Costly Opening" in recommendation.title
        assert "70" in recommendation.explanation

    def test_an_unclassified_bucket_is_never_recommended(self):
        """"Work on your unknown opening" is not something anyone can act on."""
        buckets = [
            self.opening(None, games=9, graded=400, acpl=200),
            self.opening("Sicilian Defense", games=3, graded=150, acpl=30),
        ]

        recommendation = of_type(build(by_opening=buckets), OPENING_PRIORITY)

        assert recommendation is not None
        assert "Sicilian Defense" in recommendation.title

    def test_equal_losses_break_on_critical_mistakes_then_name(self):
        buckets = [
            self.opening("Zebra Opening", games=3, graded=150, acpl=50, critical=1),
            self.opening("Alpha Opening", games=3, graded=150, acpl=50, critical=1),
            self.opening("Middle Opening", games=3, graded=150, acpl=50, critical=4),
        ]

        recommendation = of_type(build(by_opening=buckets), OPENING_PRIORITY)

        assert "Middle Opening" in recommendation.title

    def test_evidence_comes_from_the_recommended_opening_only(self):
        buckets = [self.opening("Costly Opening", games=3, graded=150, acpl=70)]
        moves = [
            review_move(ply=1, opening="Costly Opening"),
            review_move(ply=3, opening="Some Other Opening"),
            review_move(ply=5, opening="Costly Opening"),
        ]

        recommendation = of_type(
            build(by_opening=buckets, review_moves=moves), OPENING_PRIORITY
        )

        assert [item.ply for item in recommendation.evidence_items] == [1, 5]

    def test_an_opening_barely_worse_than_the_library_is_not_named(self):
        """Clearing the sample floors says the bucket is big enough to look at.
        It does not say there is a difference worth a training session.

        This is the case the real library actually produces: two games, an
        average of 24 against a library average of 20. Naming it would dress
        four centipawns of noise up as a plan.
        """
        buckets = [self.opening("Sicilian Defense", games=2, graded=61, acpl=24)]

        result = build(by_opening=buckets, overall=metrics(average_centipawn_loss=20))

        assert of_type(result, OPENING_PRIORITY) is None

    def test_a_gap_that_is_large_absolutely_but_small_relatively_is_not_named(self):
        buckets = [self.opening("Sicilian Defense", games=4, graded=200, acpl=106)]

        result = build(by_opening=buckets, overall=metrics(average_centipawn_loss=100))

        assert of_type(result, OPENING_PRIORITY) is None

    def test_counts_read_correctly_when_there_is_only_one(self):
        buckets = [self.opening("Costly Opening", games=1, graded=150, acpl=70, critical=1)]
        buckets += [self.opening("Other Opening", games=2, graded=150, acpl=70, critical=1)]

        recommendation = of_type(build(by_opening=buckets), OPENING_PRIORITY)

        assert "1 critical mistake." in recommendation.evidence_summary
        assert "1 critical mistakes" not in recommendation.evidence_summary

    def test_does_not_promise_positions_it_does_not_have(self):
        """A high average with no single move over the mistake threshold is a
        real finding - many small losses - and the wording has to say so
        rather than point at an empty list."""
        buckets = [self.opening("Costly Opening", games=3, graded=150, acpl=70)]

        recommendation = of_type(build(by_opening=buckets), OPENING_PRIORITY)

        assert recommendation.evidence_items == []
        assert "many small losses" in recommendation.explanation
        assert "positions below" not in recommendation.explanation

    def test_compares_against_the_library_average(self):
        buckets = [self.opening("Costly Opening", games=3, graded=150, acpl=70)]

        recommendation = of_type(
            build(by_opening=buckets, overall=metrics(average_centipawn_loss=22)),
            OPENING_PRIORITY,
        )

        assert "22" in recommendation.explanation


# --- colour priority -------------------------------------------------------


class TestColorPriority:
    def color(self, name, *, games=COLOR_MINIMUM_GAMES, graded=COLOR_MINIMUM_GRADED_MOVES, acpl=20):
        return ColorMoveQualityOut(
            color=name,
            metrics=metrics(games=games, graded_moves=graded, average_centipawn_loss=acpl),
        )

    def test_one_colour_alone_is_never_compared(self):
        assert of_type(build(by_color=[self.color("white")]), COLOR_PRIORITY) is None

    def test_both_sides_must_clear_the_game_floor(self):
        rows = [
            self.color("white", games=20, acpl=40),
            self.color("black", games=COLOR_MINIMUM_GAMES - 1, acpl=15),
        ]

        assert of_type(build(by_color=rows), COLOR_PRIORITY) is None

    def test_both_sides_must_clear_the_move_floor(self):
        rows = [
            self.color("white", acpl=40),
            self.color("black", graded=COLOR_MINIMUM_GRADED_MOVES - 1, acpl=15),
        ]

        assert of_type(build(by_color=rows), COLOR_PRIORITY) is None

    def test_a_small_absolute_gap_is_not_reported(self):
        rows = [self.color("white", acpl=24), self.color("black", acpl=20)]

        assert of_type(build(by_color=rows), COLOR_PRIORITY) is None

    def test_a_small_relative_gap_is_not_reported(self):
        """Five centipawns on a base of 100 is not the same finding as five on
        a base of 20, and only the relative test can tell them apart."""
        rows = [self.color("white", acpl=105), self.color("black", acpl=100)]

        assert of_type(build(by_color=rows), COLOR_PRIORITY) is None

    def test_names_the_weaker_colour_when_the_gap_is_real(self):
        rows = [self.color("white", acpl=20), self.color("black", acpl=35)]

        recommendation = of_type(build(by_color=rows), COLOR_PRIORITY)

        assert recommendation is not None
        assert "black" in recommendation.title
        assert "35" in recommendation.explanation and "20" in recommendation.explanation

    def test_evidence_comes_from_the_weaker_colour_only(self):
        rows = [self.color("white", acpl=20), self.color("black", acpl=35)]
        moves = [
            review_move(ply=1, color="white"),
            review_move(ply=2, color="black"),
            review_move(ply=4, color="black"),
        ]

        recommendation = of_type(build(by_color=rows, review_moves=moves), COLOR_PRIORITY)

        assert [item.ply for item in recommendation.evidence_items] == [2, 4]


# --- the list as a whole ---------------------------------------------------


def rich_inputs() -> dict:
    """A library with enough evidence for every rule to fire.

    Built fresh on each call so the determinism test compares two independent
    runs rather than one result against itself.
    """
    return {
        "coverage": coverage(total=40, complete=30, not_gradable=2),
        "overall": metrics(average_centipawn_loss=22),
        "by_color": [
            ColorMoveQualityOut(
                color="white",
                metrics=metrics(games=15, graded_moves=600, average_centipawn_loss=20),
            ),
            ColorMoveQualityOut(
                color="black",
                metrics=metrics(games=15, graded_moves=600, average_centipawn_loss=35),
            ),
        ],
        "by_opening": [
            OpeningMoveQualityOut(
                opening="Sicilian Defense",
                eco="B90",
                metrics=metrics(games=4, graded_moves=180, average_centipawn_loss=48),
            )
        ],
        "review_moves": [
            review_move(
                ply=1 + 2 * index,
                loss=400 - index,
                before=cp(250),
                opening="Sicilian Defense",
                color="black" if index % 2 else "white",
            )
            for index in range(8)
        ],
    }


@pytest.fixture
def rich():
    return build_recommendations(**rich_inputs())


class TestTheList:
    def test_priorities_are_contiguous_and_follow_the_fixed_order(self, rich):
        assert [r.priority for r in rich] == list(range(1, len(rich) + 1))
        assert [r.type for r in rich] == [
            name for name in RECOMMENDATION_ORDER if name in {r.type for r in rich}
        ]

    def test_every_recommendation_states_its_sample(self, rich):
        for recommendation in rich:
            assert recommendation.coverage_note
            assert "30 of 40 games" in recommendation.coverage_note
            # Games that could not be graded are declared, not quietly dropped.
            assert "2 games were saved under a name" in recommendation.coverage_note

    def test_every_recommendation_carries_evidence(self, rich):
        for recommendation in rich:
            assert recommendation.evidence_items
            assert len(recommendation.evidence_items) <= EVIDENCE_LIMIT
            assert recommendation.evidence_summary

    def test_claims_no_chess_motif(self, rich):
        """The wording rule, enforced against a denylist so it cannot regress
        through someone editing a template into something more confident.

        The system measures how far an evaluation fell. It does not identify
        motifs, and must not imply that it does.
        """
        forbidden = (
            "fork",
            "pin",
            "skewer",
            "discovered",
            "zugzwang",
            "tactic",
            "tactics",
            "tactical",
            "sacrifice",
            "endgame",
            "middlegame",
            "positional",
            "prophylaxis",
            "weak square",
            "king safety",
            "time trouble",
        )

        for recommendation in rich:
            text = " ".join(
                (
                    recommendation.title,
                    recommendation.explanation,
                    recommendation.evidence_summary,
                    recommendation.coverage_note,
                )
            ).lower()
            for term in forbidden:
                assert not re.search(rf"\b{re.escape(term)}\b", text), (
                    f"{recommendation.type} claims a motif: {term!r}"
                )

    def test_is_deterministic(self, rich):
        """Two runs over the same library must be byte-identical.

        Every ordering in the module has a total tie-break for this reason: a
        list that reshuffles between refreshes teaches the reader to distrust
        it long before they notice why.
        """
        assert build_recommendations(**rich_inputs()) == rich
