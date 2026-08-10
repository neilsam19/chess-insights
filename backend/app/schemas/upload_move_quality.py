import uuid
from typing import Literal

from pydantic import BaseModel

from app.schemas.move_quality import MoveQualitySummaryOut, PlayerScoreOut


class MoveQualityMetricsOut(MoveQualitySummaryOut):
    """One game's summary shape, plus how many games it was computed over.

    Deliberately a subclass rather than a parallel model: an upload average and
    a game average must mean the same thing, and two independent field lists
    would eventually disagree about what `graded_moves` counts.
    """

    games: int


class UploadCoverageOut(BaseModel):
    """Which games the metrics above are - and are not - based on.

    Every bucket is mutually exclusive and they sum to `total_games`, so a
    reader can always account for the difference between "79 games" and the
    number actually behind a figure. 40 analysed games must never render as 79.
    """

    total_games: int
    complete_games: int
    queued_games: int
    running_games: int
    failed_games: int
    cancelled_games: int
    not_analyzed_games: int
    # Saved under a player name that matched neither side, so there is nobody
    # to grade. Excluded from every metric rather than defaulted to White.
    not_gradable_games: int

    graded_player_moves: int
    numeric_cpl_moves: int


class ColorMoveQualityOut(BaseModel):
    color: Literal["white", "black"]
    metrics: MoveQualityMetricsOut


class OpeningMoveQualityOut(BaseModel):
    """One opening bucket. `opening` is null for games with no classification.

    A structural null rather than the string "Unknown", which would be
    indistinguishable from an opening actually called that.
    """

    opening: str | None
    # Populated only when every game in the bucket shares one ECO code, so it
    # is never presented as authoritative for a bucket that spans several.
    eco: str | None
    metrics: MoveQualityMetricsOut


class ReviewMoveOut(BaseModel):
    """One move worth looking at again, with enough to link straight to it."""

    game_id: uuid.UUID
    game_index: int
    ply: int
    move_number: int
    side: Literal["white", "black"]
    san: str
    player_color: Literal["white", "black"]
    opening: str | None
    eco: str | None
    best_move_uci: str | None
    # What the player's position was worth before and after. Carried because a
    # mistake made from a winning position is a different problem from one made
    # while already losing, and only the score can tell them apart.
    score_before: PlayerScoreOut | None
    score_after: PlayerScoreOut | None
    centipawn_loss: int | None
    quality: Literal["mistake", "blunder", "critical"]
    special_classification: Literal["missed_mate", "allowed_mate"] | None


class TrainingRecommendationOut(BaseModel):
    """One thing to work on, derived from stored facts and nothing else.

    There is no model behind this and no generated prose: every field is
    assembled from counts this response already reports, and every claim is
    backed by `evidence_items` the reader can open. `coverage_note` states the
    sample the recommendation rests on, so a conclusion drawn from four games
    cannot be mistaken for one drawn from forty.
    """

    type: Literal[
        "review_mistakes",
        "conversion_practice",
        "opening_priority",
        "color_priority",
    ]
    # 1 is the most important. Assigned after filtering, so the numbers are
    # always 1..n with no gaps where a rule declined to fire.
    priority: int
    title: str
    explanation: str
    evidence_summary: str
    evidence_items: list[ReviewMoveOut]
    coverage_note: str


class UploadMoveQualityOut(BaseModel):
    upload_id: uuid.UUID
    # Which set of thresholds and formulas produced these values.
    move_quality_version: int
    coverage: UploadCoverageOut
    overall: MoveQualityMetricsOut
    # Only colours and openings with at least one graded game appear. An empty
    # bucket rendered as zeroes reads as a result rather than as absent data.
    by_color: list[ColorMoveQualityOut]
    by_opening: list[OpeningMoveQualityOut]
    top_review_moves: list[ReviewMoveOut]
    # Empty when the evidence does not support anything. Filling the space with
    # generic advice would be the one thing this sprint set out not to do.
    recommendations: list[TrainingRecommendationOut]
