"""One independently claimable unit of engine work: analyze one game.

This is the queue. A job is claimed with `FOR UPDATE SKIP LOCKED`, held under a
time-limited lease, and released by the worker that owns it. The state machine
is documented in `docs/adr/0001-durable-analysis-jobs.md` section 4 and is the
authority for which transitions exist.

Two invariants are enforced by the database rather than by service code,
because service code cannot win a race against itself:

  * `uq_analysis_jobs_active_game_config` - at most one queued/running job may
    exist for a given (game, configuration). Two concurrent enqueues cannot
    both create one.
  * `ck_analysis_jobs_progress_bounds` - progress can never exceed the number
    of positions, and can never be negative.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.analysis_batch import AnalysisBatch
    from app.models.game import Game

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_COMPLETE = "complete"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

JOB_STATUSES = frozenset({JOB_QUEUED, JOB_RUNNING, JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED})

# Statuses that occupy the "one active job per (game, config)" slot.
JOB_ACTIVE_STATUSES = frozenset({JOB_QUEUED, JOB_RUNNING})
JOB_TERMINAL_STATUSES = frozenset({JOB_COMPLETE, JOB_FAILED, JOB_CANCELLED})

DEFAULT_MAX_ATTEMPTS = 2


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        # The database is the final arbiter of active-job uniqueness. Service
        # code checks it too, for a friendly 409, but this is what makes two
        # simultaneous enqueues safe.
        Index(
            "uq_analysis_jobs_active_game_config",
            "game_id",
            "config_hash",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        # Claim query: oldest eligible queued job.
        Index("ix_analysis_jobs_claim", "status", "available_at", "created_at"),
        # Batch progress aggregation.
        Index("ix_analysis_jobs_batch_id_status", "batch_id", "status"),
        # A game's job history, newest first.
        Index("ix_analysis_jobs_game_id_created_at", "game_id", text("created_at DESC")),
        CheckConstraint(
            "completed_positions >= 0 AND completed_positions <= total_positions",
            name="ck_analysis_jobs_progress_bounds",
        ),
        CheckConstraint("total_positions > 0", name="ck_analysis_jobs_positions_positive"),
        CheckConstraint("max_attempts >= 1", name="ck_analysis_jobs_max_attempts_positive"),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_analysis_jobs_attempt_bounds",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_batches.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)

    # Engine identity combined with this game's hash. Two jobs with the same
    # config_hash would produce identical results, which is what makes reuse
    # and duplicate rejection meaningful.
    config_hash: Mapped[str] = mapped_column(String, nullable=False)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_ATTEMPTS
    )

    # Claim eligibility. Set into the future to delay a retry.
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Lease ownership. All three are cleared whenever the job stops running,
    # so a stale worker can be told apart from a live one.
    claimed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # A control flag, not a status. A queued job is cancelled outright; a
    # running job is asked, and cancels itself at the next position boundary.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    total_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Structured code drives retry policy; the message is the sanitized text
    # shown to a user. See ADR section 6.
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    batch: Mapped["AnalysisBatch"] = relationship(back_populates="jobs")
    game: Mapped["Game"] = relationship(back_populates="analysis_jobs")
