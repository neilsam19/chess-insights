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


class JobProgressOut(BaseModel):
    """How far through a game an attempt has got.

    `percent` is computed server-side so every client shows the same number
    rather than each rounding it differently.
    """

    completed_positions: int
    total_positions: int
    percent: int
    attempt: int
    max_attempts: int


class NotAnalyzedOut(BaseModel):
    status: Literal["not_analyzed"] = "not_analyzed"
    game_id: uuid.UUID


class QueuedAnalysisOut(BaseModel):
    """Accepted and durable, but no worker has picked it up yet.

    There is no `run_id`: a run records engine provenance and cannot exist
    until an engine is actually running.
    """

    status: Literal["queued"] = "queued"
    game_id: uuid.UUID
    job_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    queued_at: datetime
    progress: JobProgressOut
    can_cancel: bool = True
    can_retry: bool = False


class RunningAnalysisOut(BaseModel):
    status: Literal["running"] = "running"
    game_id: uuid.UUID
    # Null only for a legacy Sprint 4 run that predates jobs.
    job_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    started_at: datetime
    progress: JobProgressOut | None = None
    # A cancellation has been asked for but the worker has not stopped yet.
    # Surfaced so a client can say "cancelling..." instead of looking like the
    # button did nothing - a running job only stops at a position boundary.
    cancel_requested: bool = False
    can_cancel: bool = True
    can_retry: bool = False


class FailedAnalysisOut(BaseModel):
    status: Literal["failed"] = "failed"
    game_id: uuid.UUID
    job_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    can_cancel: bool = False
    can_retry: bool = False


class CancelledAnalysisOut(BaseModel):
    """Stopped by the user. Distinct from failed - nothing went wrong."""

    status: Literal["cancelled"] = "cancelled"
    game_id: uuid.UUID
    job_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    cancelled_at: datetime | None = None
    can_cancel: bool = False
    can_retry: bool = True


class CompleteAnalysisOut(BaseModel):
    status: Literal["complete"] = "complete"
    game_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    engine: EngineMetadataOut
    started_at: datetime
    completed_at: datetime | None = None
    # Which side `score` is expressed from. Falls back to "white" when the
    # saved player's colour could not be determined.
    perspective: Literal["white", "black"]
    evaluations: list[PositionEvaluationOut]
    can_cancel: bool = False
    # A completed run is immutable. Re-analysing means a new job under a
    # different configuration, never reopening this one.
    can_retry: bool = False


GameAnalysisOut = Annotated[
    Union[
        NotAnalyzedOut,
        QueuedAnalysisOut,
        RunningAnalysisOut,
        FailedAnalysisOut,
        CancelledAnalysisOut,
        CompleteAnalysisOut,
    ],
    Field(discriminator="status"),
]


class JobStatusOut(BaseModel):
    """A job on its own, without the game's evaluations.

    Used for polling: a client watching progress should not re-download every
    evaluation on each poll.
    """

    job_id: uuid.UUID
    game_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    status: Literal["queued", "running", "complete", "failed", "cancelled"]
    progress: JobProgressOut
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested: bool = False
    can_cancel: bool
    can_retry: bool


class EnqueuedOut(BaseModel):
    """What a client gets back when analysis is accepted but not yet done."""

    job_id: uuid.UUID
    game_id: uuid.UUID
    batch_id: uuid.UUID
    status: Literal["queued", "running"]
    progress: JobProgressOut


class EngineStatusOut(BaseModel):
    available: bool
    engine_name: str | None = None
    engine_version: str | None = None
    configured_depth: int
    detail: str | None = None
