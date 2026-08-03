import uuid
from typing import Literal

from pydantic import BaseModel

from app.schemas.move_quality import MoveQualitySummaryOut


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
    centipawn_loss: int | None
    quality: Literal["mistake", "blunder", "critical"]
    special_classification: Literal["missed_mate", "allowed_mate"] | None


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
