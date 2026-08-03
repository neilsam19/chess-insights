"""Per-move quality derived from stored evaluations.

Pure functions only: no database, no engine, no I/O, no ORM. Callers adapt
persisted rows into `PlayedMove` / `StoredScore` and get facts back. That
separation is what lets every rule here be tested exhaustively without a
Postgres container or a Stockfish binary, and it keeps the interpretation of an
evaluation independent of how it was fetched.

Two things drive almost every decision in this module, both from ADR 0002:

* **A mate is never a number.** Scores normalize into three *kinds* - an
  ordinary centipawn assessment, a forced mate for the player, a forced mate
  against the player - so it is structurally impossible to subtract a mate from
  a centipawn score or to invent a "mate is worth 10000" constant.

* **`mate_in_white = 0` means checkmate is on the board, and carries no sign.**
  python-chess reports it identically whoever was mated, and the player
  perspective flip cannot help because -1 * 0 == 0. Ply parity resolves it
  exactly: the side that moved *into* a position is the side that delivered the
  mate. Without this, a player's own winning checkmate reads as a missed mate.

Index conventions, verified against the stored data and restated in ADR 0002
section 3.1:

    Move.ply               1..N, odd = White's move
    PositionEvaluation.ply 0..N, the position *after* j plies
    move k                 transitions position k-1 -> position k
    best move for move k   lives on the evaluation at ply k-1

Thresholds and formulas are server-owned constants. None of them is ever a
request parameter, for the same reason an engine path or a depth is not.
"""

from dataclasses import dataclass
from typing import Sequence

# Bump when a threshold, a formula, the transition table, or a coverage
# definition changes, so a transmitted evidence payload can always be traced
# to the rules that produced it. Independent of ANALYSIS_BEHAVIOR_VERSION,
# which identifies how evaluations were *produced*; this identifies how they
# are *interpreted*.
MOVE_QUALITY_VERSION = 1

WHITE = "white"
BLACK = "black"
GRADABLE_COLORS = (WHITE, BLACK)

# Quality labels.
GOOD = "good"
INACCURACY = "inaccuracy"
MISTAKE = "mistake"
BLUNDER = "blunder"
# A mate error. Kept distinct from `blunder` because a blunder is defined by a
# centipawn threshold and a mate error has no centipawn value at all - merging
# them would either double-count in the critical total or force a fake number.
CRITICAL = "critical"
UNSCORED = "unscored"

# Centipawn-loss thresholds. Each is the inclusive lower bound of its label.
INACCURACY_THRESHOLD = 50
MISTAKE_THRESHOLD = 100
BLUNDER_THRESHOLD = 200

# Special classifications, reported alongside the quality label rather than
# replacing it.
MISSED_MATE = "missed_mate"
ALLOWED_MATE = "allowed_mate"
MATE_SEQUENCE = "mate_sequence"

# Why a move could not be graded. Distinguished so tests and diagnostics can
# tell them apart, even where the UI groups them all as "not scored".
MISSING_BEFORE_EVALUATION = "missing_before_evaluation"
MISSING_AFTER_EVALUATION = "missing_after_evaluation"
NON_CONTIGUOUS_EVALUATIONS = "non_contiguous_evaluations"
AMBIGUOUS_MATE = "ambiguous_mate"
MISSING_MOVE_DATA = "missing_move_data"
# MATE_SEQUENCE doubles as a reason code: it is both what happened and why
# there is no safe centipawn loss.

# Score kinds. The sign is folded into the kind so that comparing two scores is
# a lookup in a 3x3 table rather than arithmetic that could silently mix types.
CP = "cp"
MATE_FOR = "mate_for"
MATE_AGAINST = "mate_against"


@dataclass(frozen=True)
class PlayedMove:
    """One move as stored, with nothing interpreted."""

    ply: int
    san: str
    uci: str


@dataclass(frozen=True)
class StoredScore:
    """One evaluation row's score exactly as persisted.

    Canonical White perspective, as written by the worker: positive centipawns
    and positive mate values favour White. Perspective is applied here, once,
    by `normalize_score`.
    """

    ply: int
    score_cp_white: int | None
    mate_in_white: int | None
    best_move_uci: str | None = None


@dataclass(frozen=True)
class PlayerScore:
    """A position assessment from the selected player's point of view.

    Exactly one of `cp` / `mate_distance` is populated, matching the storage
    constraint. `mate_distance` of 0 means checkmate is already on the board.
    """

    kind: str
    cp: int | None = None
    mate_distance: int | None = None

    @property
    def is_mate(self) -> bool:
        return self.kind in (MATE_FOR, MATE_AGAINST)


@dataclass(frozen=True)
class MoveQuality:
    """Everything derived about one move by the selected player."""

    ply: int
    move_number: int
    side: str
    san: str
    played_move_uci: str
    best_move_uci: str | None
    # None - never False - when there is no best move to compare against.
    is_exact_best_move: bool | None
    score_before: PlayerScore | None
    score_after: PlayerScore | None
    centipawn_loss: int | None
    quality: str
    special_classification: str | None
    unscored_reason: str | None

    @property
    def is_graded(self) -> bool:
        return self.quality != UNSCORED

    @property
    def is_critical(self) -> bool:
        """A blunder or a mate error - the moves worth reviewing first."""
        return self.quality in (BLUNDER, CRITICAL)


def side_for_ply(ply: int) -> str:
    """Which side made the move at `ply`. Move plies are 1-based."""
    return WHITE if ply % 2 == 1 else BLACK


def move_number_for_ply(ply: int) -> int:
    return (ply + 1) // 2


def normalize_uci(uci: str | None) -> str | None:
    """Comparable form of a UCI move.

    Case and surrounding whitespace are normalized; nothing else is. The
    promotion suffix in particular stays part of the string, because e7e8q and
    e7e8n are different moves and one of them is usually much worse.
    """
    if uci is None:
        return None
    stripped = uci.strip().lower()
    return stripped or None


def normalize_score(
    score_cp_white: int | None,
    mate_in_white: int | None,
    ply: int,
    *,
    player_is_white: bool,
) -> PlayerScore | None:
    """Convert a stored score into the player's perspective and kind.

    Returns None when a `mate 0` cannot be attributed to a side - which needs
    ply parity, and so is impossible at ply 0. Guessing would be worse than
    reporting the move as unscored.

    Raises ValueError if neither score is populated. The database check
    constraint makes that unreachable for stored rows; it is a loud failure
    rather than a silent "no evaluation" precisely because it should never
    happen.
    """
    if mate_in_white is None and score_cp_white is None:
        raise ValueError("Stored evaluation has neither a centipawn nor a mate score.")

    if mate_in_white is None:
        assert score_cp_white is not None  # narrowed by the check above
        cp = score_cp_white if player_is_white else -score_cp_white
        return PlayerScore(kind=CP, cp=cp)

    if mate_in_white == 0:
        # Checkmate on the board. The mated side is the side to move, so the
        # side that *delivered* it is the side that moved into this position -
        # White at an odd ply. Ply 0 is the starting position and can never be
        # checkmate, so a mate 0 there is malformed rather than attributable.
        if ply <= 0:
            return None
        mater_is_white = ply % 2 == 1
        distance = 0
    else:
        mater_is_white = mate_in_white > 0
        distance = abs(mate_in_white)

    kind = MATE_FOR if mater_is_white == player_is_white else MATE_AGAINST
    return PlayerScore(kind=kind, mate_distance=distance)


def quality_for_loss(centipawn_loss: int) -> str:
    """Label a numeric centipawn loss. Boundaries are inclusive lower bounds."""
    if centipawn_loss >= BLUNDER_THRESHOLD:
        return BLUNDER
    if centipawn_loss >= MISTAKE_THRESHOLD:
        return MISTAKE
    if centipawn_loss >= INACCURACY_THRESHOLD:
        return INACCURACY
    return GOOD


def _unscored(
    move: PlayedMove,
    reason: str,
    *,
    best_move_uci: str | None = None,
    is_exact_best_move: bool | None = None,
    score_before: PlayerScore | None = None,
    score_after: PlayerScore | None = None,
    special_classification: str | None = None,
) -> MoveQuality:
    return MoveQuality(
        ply=move.ply,
        move_number=move_number_for_ply(move.ply),
        side=side_for_ply(move.ply),
        san=move.san,
        played_move_uci=move.uci,
        best_move_uci=best_move_uci,
        is_exact_best_move=is_exact_best_move,
        score_before=score_before,
        score_after=score_after,
        centipawn_loss=None,
        quality=UNSCORED,
        special_classification=special_classification,
        unscored_reason=reason,
    )


def _grade_transition(before: PlayerScore, after: PlayerScore) -> tuple[int | None, str, str | None]:
    """The ADR 0002 transition table, as `(centipawn_loss, quality, special)`.

    Three of the nine cells are ordinary events that are not mistakes: finding
    a forced mate, escaping one, and being lost before and after. None of them
    may be assigned a centipawn value, and none of them counts against the
    player.
    """
    if before.kind == CP and after.kind == CP:
        assert before.cp is not None and after.cp is not None
        # Clamped: a move that improves the position is not assigned negative
        # loss, it simply loses nothing.
        loss = max(0, before.cp - after.cp)
        return loss, quality_for_loss(loss), None

    if before.kind == MATE_FOR and after.kind == MATE_FOR:
        # Still mating. A change in mate distance is not an error, and there is
        # no honest centipawn value for it either way.
        return None, UNSCORED, MATE_SEQUENCE

    if before.kind == MATE_AGAINST and after.kind == MATE_AGAINST:
        # Already lost and still lost. Not a new mistake.
        return None, UNSCORED, MATE_SEQUENCE

    if after.kind == MATE_AGAINST:
        # The opponent had no forced mate and now does.
        return None, CRITICAL, ALLOWED_MATE

    if before.kind == MATE_FOR and after.kind == CP:
        # Had a forced mate, threw it away.
        return None, CRITICAL, MISSED_MATE

    # Everything remaining is an improvement: a mate found from a centipawn
    # position, or a mate escaped. Not gradable numerically, not a mistake.
    return None, GOOD, None


def classify_move(
    move: PlayedMove,
    before: StoredScore | None,
    after: StoredScore | None,
    *,
    player_is_white: bool,
) -> MoveQuality:
    """Derive the facts about one move of the selected player.

    `before` is the evaluation of the position the player faced (ply k-1) and
    carries the engine's best move for it; `after` is the evaluation of the
    position their move produced (ply k).
    """
    if not move.uci or not move.san:
        return _unscored(move, MISSING_MOVE_DATA)
    if before is None:
        return _unscored(move, MISSING_BEFORE_EVALUATION)
    if after is None:
        return _unscored(move, MISSING_AFTER_EVALUATION)
    if before.ply != move.ply - 1 or after.ply != move.ply:
        # The caller paired this move with evaluations of other positions.
        # Grading them would produce a confident, wrong number.
        return _unscored(move, NON_CONTIGUOUS_EVALUATIONS)

    best_move_uci = normalize_uci(before.best_move_uci)
    played_uci = normalize_uci(move.uci)
    is_exact_best_move = None if best_move_uci is None else played_uci == best_move_uci

    score_before = normalize_score(
        before.score_cp_white, before.mate_in_white, before.ply, player_is_white=player_is_white
    )
    score_after = normalize_score(
        after.score_cp_white, after.mate_in_white, after.ply, player_is_white=player_is_white
    )
    if score_before is None or score_after is None:
        return _unscored(
            move,
            AMBIGUOUS_MATE,
            best_move_uci=best_move_uci,
            is_exact_best_move=is_exact_best_move,
            score_before=score_before,
            score_after=score_after,
        )

    centipawn_loss, quality, special = _grade_transition(score_before, score_after)

    return MoveQuality(
        ply=move.ply,
        move_number=move_number_for_ply(move.ply),
        side=side_for_ply(move.ply),
        san=move.san,
        played_move_uci=move.uci,
        best_move_uci=best_move_uci,
        is_exact_best_move=is_exact_best_move,
        score_before=score_before,
        score_after=score_after,
        centipawn_loss=centipawn_loss,
        quality=quality,
        special_classification=special,
        # A mate sequence is reported as both what happened and why it could
        # not be scored, so a client never has to infer one from the other.
        unscored_reason=MATE_SEQUENCE if special == MATE_SEQUENCE else None,
    )


def derive_move_quality(
    moves: Sequence[PlayedMove],
    evaluations: Sequence[StoredScore],
    player_color: str,
) -> list[MoveQuality]:
    """Facts for every move made by the selected player, in ply order.

    The opponent's moves are not graded and do not appear in the result: this
    is personal analytics, and presenting an opponent's quality as the user's
    feedback would be worse than presenting nothing.

    Raises ValueError for a player colour that is not white or black. A game
    whose saved player matched neither side has no player perspective to take,
    and defaulting to White would silently report someone else's moves as the
    user's.
    """
    if player_color not in GRADABLE_COLORS:
        raise ValueError(f"Move quality requires a known player colour, got {player_color!r}.")

    player_is_white = player_color == WHITE
    by_ply = {evaluation.ply: evaluation for evaluation in evaluations}

    return [
        classify_move(
            move,
            by_ply.get(move.ply - 1),
            by_ply.get(move.ply),
            player_is_white=player_is_white,
        )
        for move in sorted(moves, key=lambda move: move.ply)
        if side_for_ply(move.ply) == player_color
    ]


@dataclass(frozen=True)
class GameMoveQualitySummary:
    """Aggregate facts about one player's moves in one game.

    Three counts describe coverage rather than one, because a move can be
    graded without having a numeric centipawn loss - every off-diagonal mate
    transition does exactly that. Conflating them would either hide mate errors
    from coverage or drag them into an average they have no number for.
    """

    total_player_moves: int
    # Quality is anything other than `unscored` - includes mate errors.
    graded_moves: int
    # Has an integer centipawn loss. The ACPL and median sample, and reported
    # alongside them so a client can see how thin that sample is.
    numeric_cpl_moves: int
    unscored_moves: int
    coverage_percent: int

    # None, never 0, when there is nothing to average: "ACPL 0" reads as
    # perfect play, which is the opposite of "we could not measure this".
    average_centipawn_loss: int | None
    median_centipawn_loss: int | None

    exact_best_move_count: int
    # Moves that had a stored best move to compare against - the rate's
    # denominator, which is not the same as the move count.
    exact_best_move_comparable: int
    exact_best_move_percent: int | None

    inaccuracies: int
    mistakes: int
    blunders: int
    missed_mates: int
    allowed_mates: int
    mate_sequences: int
    # Blunders plus mate errors. Disjoint labels, so nothing is counted twice.
    critical_mistakes: int


def _percent(numerator: int, denominator: int) -> int:
    """Rounded half away from zero, so a displayed percentage never surprises.

    Python's built-in round() is half-to-even, which would make 12.5 and 13.5
    round in different directions - defensible for statistics, confusing in a
    UI that shows both.
    """
    return int(100 * numerator / denominator + 0.5)


def _mean(values: list[int]) -> int:
    return int(sum(values) / len(values) + 0.5)


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return int((ordered[middle - 1] + ordered[middle]) / 2 + 0.5)


def summarize_moves(facts: Sequence[MoveQuality]) -> GameMoveQualitySummary:
    """Aggregate per-move facts. Pure, and safe on an empty sequence.

    Mate events never enter the centipawn average - there is no honest number
    for them - but they do count as graded and as critical mistakes. A player
    who missed three forced mates has not played a clean game, and an ACPL that
    ignored them while a coverage figure also ignored them would say otherwise.
    """
    total = len(facts)
    losses = [fact.centipawn_loss for fact in facts if fact.centipawn_loss is not None]
    graded = sum(1 for fact in facts if fact.is_graded)

    comparable = [fact for fact in facts if fact.is_exact_best_move is not None]
    exact = sum(1 for fact in comparable if fact.is_exact_best_move)

    def count(**criteria: str) -> int:
        field, expected = next(iter(criteria.items()))
        return sum(1 for fact in facts if getattr(fact, field) == expected)

    blunders = count(quality=BLUNDER)
    missed_mates = count(special_classification=MISSED_MATE)
    allowed_mates = count(special_classification=ALLOWED_MATE)

    return GameMoveQualitySummary(
        total_player_moves=total,
        graded_moves=graded,
        numeric_cpl_moves=len(losses),
        unscored_moves=total - graded,
        coverage_percent=_percent(graded, total) if total else 0,
        average_centipawn_loss=_mean(losses) if losses else None,
        median_centipawn_loss=_median(losses) if losses else None,
        exact_best_move_count=exact,
        exact_best_move_comparable=len(comparable),
        exact_best_move_percent=_percent(exact, len(comparable)) if comparable else None,
        inaccuracies=count(quality=INACCURACY),
        mistakes=count(quality=MISTAKE),
        blunders=blunders,
        missed_mates=missed_mates,
        allowed_mates=allowed_mates,
        mate_sequences=count(special_classification=MATE_SEQUENCE),
        critical_mistakes=blunders + missed_mates + allowed_mates,
    )


# Mistake and worse. Inaccuracies are excluded deliberately: a list meant to
# answer "what should I look at first" stops being useful once it contains
# every slightly imprecise move.
REVIEWABLE_QUALITIES = frozenset({MISTAKE, BLUNDER, CRITICAL})


def is_reviewable(fact: MoveQuality) -> bool:
    return fact.quality in REVIEWABLE_QUALITIES


def review_severity_key(fact: MoveQuality) -> tuple[int, int]:
    """Sort key for "worst first". Lower sorts earlier.

    Mate errors come before every centipawn loss rather than being given a
    notional pawn value. Throwing away a forced mate, or walking into one, is a
    different kind of event from losing material, and the whole module exists
    to avoid pricing mate in pawns.

    This is a *partial* order - two mate errors tie, as do two equal losses.
    Callers must append their own stable tiebreak, or the list will reorder
    between requests for no reason the reader can see.
    """
    return (
        0 if fact.special_classification is not None else 1,
        -(fact.centipawn_loss or 0),
    )


__all__ = [
    "ALLOWED_MATE",
    "AMBIGUOUS_MATE",
    "BLACK",
    "BLUNDER",
    "BLUNDER_THRESHOLD",
    "CP",
    "CRITICAL",
    "GOOD",
    "GRADABLE_COLORS",
    "GameMoveQualitySummary",
    "INACCURACY",
    "INACCURACY_THRESHOLD",
    "MATE_AGAINST",
    "MATE_FOR",
    "MATE_SEQUENCE",
    "MISSED_MATE",
    "MISSING_AFTER_EVALUATION",
    "MISSING_BEFORE_EVALUATION",
    "MISSING_MOVE_DATA",
    "MISTAKE",
    "MISTAKE_THRESHOLD",
    "MOVE_QUALITY_VERSION",
    "MoveQuality",
    "NON_CONTIGUOUS_EVALUATIONS",
    "PlayedMove",
    "PlayerScore",
    "REVIEWABLE_QUALITIES",
    "StoredScore",
    "UNSCORED",
    "WHITE",
    "classify_move",
    "derive_move_quality",
    "is_reviewable",
    "move_number_for_ply",
    "normalize_score",
    "normalize_uci",
    "quality_for_loss",
    "review_severity_key",
    "side_for_ply",
    "summarize_moves",
]
