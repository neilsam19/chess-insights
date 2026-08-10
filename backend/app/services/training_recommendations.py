"""What to work on, decided by rules over stored facts.

No model, no prompt, no generated prose. Every recommendation here is assembled
from counts the move-quality response already reports, and every claim it makes
is backed by evidence items the reader can open and check. The module does no
I/O: it takes the response objects and returns more response objects, which is
what lets each rule be tested against a handful of hand-built inputs.

Three rules govern the whole file.

**Say only what the data supports.** The system measures how far the engine's
assessment of a position fell after a move. It does not identify chess motifs,
so it must never claim one. "Review these positions" is a statement about
stored numbers; "you are weak at forks" is an invention. A test asserts every
generated string against a denylist of motif terms, so this cannot regress
through someone editing a template.

**Omission is a valid answer.** Each rule has an evidence floor and declines
below it. Insufficient data produces fewer recommendations, never filler - a
library of four games has nothing useful to say about which colour you play
worse, and saying it anyway would be worse than saying nothing.

**Every threshold is a server-owned constant.** None of them is a request
parameter, for exactly the reason an engine path or a depth is not.
"""

from app.schemas.upload_move_quality import (
    ColorMoveQualityOut,
    MoveQualityMetricsOut,
    OpeningMoveQualityOut,
    ReviewMoveOut,
    TrainingRecommendationOut,
    UploadCoverageOut,
)

REVIEW_MISTAKES = "review_mistakes"
CONVERSION_PRACTICE = "conversion_practice"
OPENING_PRIORITY = "opening_priority"
COLOR_PRIORITY = "color_priority"

# Emission order. Priority numbers are assigned from this after filtering, so
# a rule declining to fire never leaves a gap in the sequence.
RECOMMENDATION_ORDER = (
    REVIEW_MISTAKES,
    CONVERSION_PRACTICE,
    OPENING_PRIORITY,
    COLOR_PRIORITY,
)

# How many positions a single recommendation carries. The standalone review
# list is longer; a recommendation is meant to be actionable in one sitting.
EVIDENCE_LIMIT = 5

# An opening needs both floors. Two games is not a pattern on its own, and
# twenty graded moves stops one short game from deciding anything.
OPENING_MINIMUM_GAMES = 2
OPENING_MINIMUM_GRADED_MOVES = 20

COLOR_MINIMUM_GAMES = 3
COLOR_MINIMUM_GRADED_MOVES = 60

# What counts as "meaningfully worse", for both the opening and the colour
# rule. Clearing an evidence floor only says the sample is big enough to look
# at; it says nothing about whether there is a difference worth naming, and
# without this a 4-centipawn gap over two games becomes a training plan.
#
# Both floors, because either alone misleads: 5 centipawns on a base of 20 is a
# real difference, 5 on a base of 100 is not, and 20% of a base of 4 is nothing.
MINIMUM_ACPL_GAP = 5
MINIMUM_RELATIVE_GAP_PERCENT = 20

# The conversion rule. A player who mostly errs while already losing has a
# different problem from one who mostly errs while winning, and only the score
# before the move distinguishes them.
CONVERSION_MINIMUM_SAMPLE = 5
CONVERSION_SHARE_THRESHOLD_PERCENT = 60
ADVANTAGE_THRESHOLD_CP = 100


def _percent(numerator: int, denominator: int) -> int:
    """Half away from zero, matching every other percentage in the sprint."""
    if denominator == 0:
        return 0
    return int(100 * numerator / denominator + 0.5)


def _is_meaningfully_worse(worse: int, baseline: int) -> bool:
    """Is one average far enough above another to be worth naming?"""
    gap = worse - baseline
    return (
        gap >= MINIMUM_ACPL_GAP
        and _percent(gap, baseline) >= MINIMUM_RELATIVE_GAP_PERCENT
    )


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _coverage_note(coverage: UploadCoverageOut) -> str:
    note = (
        f"Based on {coverage.complete_games} of {coverage.total_games} games with "
        f"completed analysis, covering {coverage.graded_player_moves} of your moves."
    )
    if coverage.not_gradable_games:
        note += (
            f" {coverage.not_gradable_games} games were saved under a name that "
            "matched neither player and could not be graded."
        )
    return note


def _pawns(centipawns: int) -> str:
    return f"{centipawns / 100:.2f}"


def _loss_range(items: list[ReviewMoveOut]) -> str:
    """A factual one-liner about a set of positions.

    Mate errors are described as mate errors rather than folded into a
    centipawn range, because they have no centipawn value to fold.
    """
    losses = [item.centipawn_loss for item in items if item.centipawn_loss is not None]
    mates = sum(1 for item in items if item.special_classification is not None)

    parts = []
    if losses:
        if len(losses) == 1:
            parts.append(f"one position losing {_pawns(losses[0])} pawns")
        else:
            parts.append(
                f"{len(losses)} positions losing between {_pawns(min(losses))} "
                f"and {_pawns(max(losses))} pawns"
            )
    if mates:
        parts.append(f"{mates} forced mate {'error' if mates == 1 else 'errors'}")

    return " and ".join(parts) if parts else "no positions"


def _where_to_start(items: list[ReviewMoveOut]) -> str:
    """How to close an aggregate recommendation.

    A high average with no individual move crossing the mistake threshold is a
    real and different finding - the loss is spread thin rather than
    concentrated - and promising positions that are not there would be the
    recommendation contradicting its own evidence list.
    """
    if items:
        return "Start by replaying the positions below."
    return (
        "No single move here crossed the mistake threshold, so this is many "
        "small losses rather than a few large ones."
    )


def _is_winning(item: ReviewMoveOut) -> bool:
    """Was the player already clearly better when they played this move?"""
    score = item.score_before
    if score is None:
        return False
    if score.kind == "mate_for":
        return True
    if score.kind == "mate_against":
        return False
    return score.cp is not None and score.cp >= ADVANTAGE_THRESHOLD_CP


def _review_mistakes(
    review_moves: list[ReviewMoveOut], coverage: UploadCoverageOut
) -> TrainingRecommendationOut | None:
    if not review_moves:
        return None

    items = review_moves[:EVIDENCE_LIMIT]
    return TrainingRecommendationOut(
        type=REVIEW_MISTAKES,
        priority=0,
        title="Review your highest-impact positions",
        explanation=(
            "These are the moves after which the engine's assessment of your "
            "position fell furthest. Each one is a specific position rather "
            "than a general weakness: open it, play through it, and work out "
            "what the stronger continuation was."
        ),
        evidence_summary=f"Your {len(items)} costliest moves - {_loss_range(items)}.",
        evidence_items=items,
        coverage_note=_coverage_note(coverage),
    )


def _conversion_practice(
    review_moves: list[ReviewMoveOut], coverage: UploadCoverageOut
) -> TrainingRecommendationOut | None:
    """Fires when the costly mistakes cluster in already-better positions.

    Deliberately not a claim about calculation ability in general - only that a
    measurable share of the damage happened from positions the engine had
    already assessed as clearly won, which is a different starting point for
    practice from defending worse ones.
    """
    known = [item for item in review_moves if item.score_before is not None]
    if len(known) < CONVERSION_MINIMUM_SAMPLE:
        return None

    winning = [item for item in known if _is_winning(item)]
    share = _percent(len(winning), len(known))
    if share < CONVERSION_SHARE_THRESHOLD_PERCENT:
        return None

    return TrainingRecommendationOut(
        type=CONVERSION_PRACTICE,
        priority=0,
        title="Practise finishing from better positions",
        explanation=(
            f"{share}% of your costly moves ({len(winning)} of {len(known)}) came "
            "from positions the engine already rated clearly in your favour. The "
            "advantage was there before the move that lost it, so these are "
            "positions worth slowing down over rather than examples of being "
            "outplayed from the start."
        ),
        evidence_summary=(
            f"{len(winning)} of {len(known)} costly moves were made from a "
            "winning position."
        ),
        evidence_items=winning[:EVIDENCE_LIMIT],
        coverage_note=_coverage_note(coverage),
    )


def _opening_priority(
    by_opening: list[OpeningMoveQualityOut],
    overall: MoveQualityMetricsOut,
    review_moves: list[ReviewMoveOut],
    coverage: UploadCoverageOut,
) -> TrainingRecommendationOut | None:
    """The weakest opening that clears both evidence floors.

    Usually there is none. A library of forty games spread over thirty
    canonical openings has no bucket large enough to conclude anything from,
    and the by-opening table is still shown with its counts. That is the
    designed outcome, not a gap.
    """
    library_acpl = overall.average_centipawn_loss
    if library_acpl is None:
        # Nothing to compare against, so nothing can be shown to be worse.
        return None

    eligible = [
        bucket
        for bucket in by_opening
        # An unclassified bucket is excluded: "work on your unknown opening" is
        # not something anyone can act on.
        if bucket.opening is not None
        and bucket.metrics.games >= OPENING_MINIMUM_GAMES
        and bucket.metrics.graded_moves >= OPENING_MINIMUM_GRADED_MOVES
        and bucket.metrics.average_centipawn_loss is not None
        # The bucket is part of the library average it is compared against,
        # which dampens the gap slightly. Erring towards silence is the right
        # direction for a rule whose output is advice.
        and _is_meaningfully_worse(bucket.metrics.average_centipawn_loss, library_acpl)
    ]
    if not eligible:
        return None

    # Weakest first; more critical mistakes, then name, break every tie.
    weakest = min(
        eligible,
        key=lambda b: (
            -(b.metrics.average_centipawn_loss or 0),
            -b.metrics.critical_mistakes,
            b.opening or "",
        ),
    )

    metrics = weakest.metrics
    items = [item for item in review_moves if item.opening == weakest.opening][
        :EVIDENCE_LIMIT
    ]

    return TrainingRecommendationOut(
        type=OPENING_PRIORITY,
        priority=0,
        title=f"Spend your next session on {weakest.opening}",
        explanation=(
            f"Across {metrics.games} games in this opening your average "
            f"centipawn loss is {metrics.average_centipawn_loss}, against "
            f"{library_acpl} across your whole library. That is a measurement "
            "of where the losses happened, not a verdict on the opening "
            f"itself. {_where_to_start(items)}"
        ),
        evidence_summary=(
            f"{_plural(metrics.games, 'game', 'games')}, "
            f"{metrics.graded_moves} graded moves, "
            f"{_plural(metrics.critical_mistakes, 'critical mistake', 'critical mistakes')}."
        ),
        evidence_items=items,
        coverage_note=_coverage_note(coverage),
    )


def _color_priority(
    by_color: list[ColorMoveQualityOut],
    review_moves: list[ReviewMoveOut],
    coverage: UploadCoverageOut,
) -> TrainingRecommendationOut | None:
    """The weaker colour, but only when both sides are a real sample.

    Comparing eleven games as White against two as Black would produce a
    confident statement about the difference between them and a meaningless
    one, so both sides must clear the floor before either is named.
    """
    if len(by_color) != 2:
        return None

    for row in by_color:
        if (
            row.metrics.games < COLOR_MINIMUM_GAMES
            or row.metrics.graded_moves < COLOR_MINIMUM_GRADED_MOVES
            or row.metrics.average_centipawn_loss is None
        ):
            return None

    weaker, stronger = sorted(
        by_color,
        key=lambda row: (-(row.metrics.average_centipawn_loss or 0), row.color),
    )

    weak_acpl = weaker.metrics.average_centipawn_loss or 0
    strong_acpl = stronger.metrics.average_centipawn_loss or 0

    if not _is_meaningfully_worse(weak_acpl, strong_acpl):
        return None

    items = [item for item in review_moves if item.player_color == weaker.color][
        :EVIDENCE_LIMIT
    ]

    return TrainingRecommendationOut(
        type=COLOR_PRIORITY,
        priority=0,
        title=f"Your games as {weaker.color} are costing you more",
        explanation=(
            f"As {weaker.color} your average centipawn loss is {weak_acpl} across "
            f"{weaker.metrics.games} games, against {strong_acpl} as "
            f"{stronger.color} across {stronger.metrics.games}. Both samples are "
            "large enough to compare, so the difference is worth a session on "
            f"its own. {_where_to_start(items)}"
        ),
        evidence_summary=(
            f"{weaker.color}: {weaker.metrics.games} games, average loss "
            f"{weak_acpl}. {stronger.color}: {stronger.metrics.games} games, "
            f"average loss {strong_acpl}."
        ),
        evidence_items=items,
        coverage_note=_coverage_note(coverage),
    )


def build_recommendations(
    *,
    coverage: UploadCoverageOut,
    overall: MoveQualityMetricsOut,
    by_color: list[ColorMoveQualityOut],
    by_opening: list[OpeningMoveQualityOut],
    review_moves: list[ReviewMoveOut],
) -> list[TrainingRecommendationOut]:
    """Every recommendation the evidence supports, most important first.

    `review_moves` is the full ranked list rather than the displayed top ten,
    so a recommendation scoped to one opening or one colour still has the best
    available positions to point at.
    """
    candidates = {
        REVIEW_MISTAKES: _review_mistakes(review_moves, coverage),
        CONVERSION_PRACTICE: _conversion_practice(review_moves, coverage),
        OPENING_PRIORITY: _opening_priority(by_opening, overall, review_moves, coverage),
        COLOR_PRIORITY: _color_priority(by_color, review_moves, coverage),
    }

    recommendations = [
        candidates[name] for name in RECOMMENDATION_ORDER if candidates[name] is not None
    ]

    return [
        recommendation.model_copy(update={"priority": index})
        for index, recommendation in enumerate(recommendations, start=1)
    ]


__all__ = [
    "ADVANTAGE_THRESHOLD_CP",
    "COLOR_MINIMUM_GAMES",
    "COLOR_MINIMUM_GRADED_MOVES",
    "COLOR_PRIORITY",
    "CONVERSION_MINIMUM_SAMPLE",
    "CONVERSION_PRACTICE",
    "CONVERSION_SHARE_THRESHOLD_PERCENT",
    "EVIDENCE_LIMIT",
    "MINIMUM_ACPL_GAP",
    "MINIMUM_RELATIVE_GAP_PERCENT",
    "OPENING_MINIMUM_GAMES",
    "OPENING_MINIMUM_GRADED_MOVES",
    "OPENING_PRIORITY",
    "RECOMMENDATION_ORDER",
    "REVIEW_MISTAKES",
    "build_recommendations",
]
