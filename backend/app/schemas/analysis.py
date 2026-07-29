import uuid
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ScoreOut(BaseModel):
    """A position assessment.

    Centipawns and forced mate stay different types on the wire - mate is
    never flattened into a large fake centipawn number.
    """

    type: Literal["cp", "mate"]
    value: int


class PositionEvaluationOut(BaseModel):
    ply: int
    # From the saved player's perspective: positive means the player is better.
    score: ScoreOut
    # Canonical White-perspective value, as persisted. Useful for debugging
    # and for clients that want the raw engine convention.
    white_score: ScoreOut
    best_move_uci: str | None = None
    depth_reached: int | None = None
    nodes: int | None = None
    time_ms: int | None = None


class EngineMetadataOut(BaseModel):
    name: str
    version: str | None = None
    binary_sha256: str | None = None
    requested_depth: int
    options: dict = Field(default_factory=dict)


class NotAnalyzedOut(BaseModel):
    status: Literal["not_analyzed"] = "not_analyzed"
    game_id: uuid.UUID


class RunningAnalysisOut(BaseModel):
    status: Literal["running"] = "running"
    game_id: uuid.UUID
    run_id: uuid.UUID
    started_at: datetime


class FailedAnalysisOut(BaseModel):
    status: Literal["failed"] = "failed"
    game_id: uuid.UUID
    run_id: uuid.UUID
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class CompleteAnalysisOut(BaseModel):
    status: Literal["complete"] = "complete"
    game_id: uuid.UUID
    run_id: uuid.UUID
    engine: EngineMetadataOut
    started_at: datetime
    completed_at: datetime | None = None
    # Which side `score` is expressed from. Falls back to "white" when the
    # saved player's colour could not be determined.
    perspective: Literal["white", "black"]
    evaluations: list[PositionEvaluationOut]


GameAnalysisOut = Annotated[
    Union[NotAnalyzedOut, RunningAnalysisOut, FailedAnalysisOut, CompleteAnalysisOut],
    Field(discriminator="status"),
]


class EngineStatusOut(BaseModel):
    available: bool
    engine_name: str | None = None
    engine_version: str | None = None
    configured_depth: int
    detail: str | None = None
