import uuid
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.schemas.analysis import (
    CancelledAnalysisOut,
    EngineMetadataOut,
    FailedAnalysisOut,
    NotAnalyzedOut,
    QueuedAnalysisOut,
    RunningAnalysisOut,
)


class PlayerScoreOut(BaseModel):
    """A position assessment from the selected player's point of view.

    Deliberately not `ScoreOut`. That shape carries a signed number, and a
    delivered checkmate is stored as `mate 0`, which is signless - so
    `{type: "mate", value: 0}` cannot say whether the player mated or was
    mated. Folding the direction into `kind` makes it explicit, and makes it
    impossible for a client to treat a mate as a number.
    """

    kind: Literal["cp", "mate_for", "mate_against"]
    # Player-relative centipawns; populated only when kind is "cp".
    cp: int | None = None
    # Moves until mate; 0 means checkmate is already on the board.
    mate_distance: int | None = None


class MoveQualityOut(BaseModel):
    ply: int
    move_number: int
    side: Literal["white", "black"]
    san: str
    played_move_uci: str
    best_move_uci: str | None = None
    # Null - never false - when there was no stored best move to compare
    # against. A move is never graded down for failing to match it.
    is_exact_best_move: bool | None = None
    score_before: PlayerScoreOut | None = None
    score_after: PlayerScoreOut | None = None
    # Null for every mate transition. Mate is never given a centipawn value.
    centipawn_loss: int | None = None
    quality: Literal["good", "inaccuracy", "mistake", "blunder", "critical", "unscored"]
    special_classification: Literal["missed_mate", "allowed_mate", "mate_sequence"] | None = None
    unscored_reason: str | None = None


class MoveQualitySummaryOut(BaseModel):
    """Aggregates over the selected player's moves in one game.

    Three counts rather than one, because a move can be graded without having a
    numeric centipawn loss: `graded_moves` drives coverage, `numeric_cpl_moves`
    is the sample behind the average and median.
    """

    total_player_moves: int
    graded_moves: int
    numeric_cpl_moves: int
    unscored_moves: int
    coverage_percent: int

    # Null, never 0, when nothing could be averaged.
    average_centipawn_loss: int | None = None
    median_centipawn_loss: int | None = None

    exact_best_move_count: int
    exact_best_move_comparable: int
    # Integer percentage, 0-100. Null when nothing was comparable.
    exact_best_move_percent: int | None = None

    inaccuracies: int
    mistakes: int
    blunders: int
    missed_mates: int
    allowed_mates: int
    mate_sequences: int
    # Blunders plus mate errors. The labels are disjoint, so nothing is
    # counted twice.
    critical_mistakes: int


class NotGradableOut(BaseModel):
    """The game has analysis, but no identifiable player to grade.

    `user_color` is "unknown" when the saved player name matched neither side.
    Grading anyway would present an opponent's moves as the user's own, so this
    is reported rather than defaulted to White.
    """

    status: Literal["not_gradable"] = "not_gradable"
    game_id: uuid.UUID
    reason: Literal["unknown_player_color"] = "unknown_player_color"
    detail: str


class GameMoveQualityOut(BaseModel):
    status: Literal["complete"] = "complete"
    game_id: uuid.UUID
    analysis_run_id: uuid.UUID
    job_id: uuid.UUID | None = None
    player_color: Literal["white", "black"]
    engine: EngineMetadataOut
    # Which set of thresholds and formulas produced these values.
    move_quality_version: int
    summary: MoveQualitySummaryOut
    moves: list[MoveQualityOut]


GameMoveQualityResponse = Annotated[
    Union[
        NotAnalyzedOut,
        QueuedAnalysisOut,
        RunningAnalysisOut,
        FailedAnalysisOut,
        CancelledAnalysisOut,
        NotGradableOut,
        GameMoveQualityOut,
    ],
    Field(discriminator="status"),
]
