"""One user request to analyze something - a single game or a whole upload.

A single-game request still creates a one-job batch, so progress reporting and
cancellation work identically everywhere rather than having two code paths.

`status` is a *derived cache*, not an independently maintained field. It is
written only by the single batch-state authority (Checkpoint 8), recomputed
from child jobs inside the same transaction as any job transition. Counts and
percentages are never stored - they are always aggregated from
`analysis_jobs`, so they cannot drift.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.analysis_job import AnalysisJob
    from app.models.upload import Upload

# Plain strings rather than a database enum, matching user_color /
# opening_source / analysis_runs.status.
BATCH_QUEUED = "queued"
BATCH_RUNNING = "running"
BATCH_COMPLETE = "complete"
BATCH_PARTIAL_FAILED = "partial_failed"
BATCH_FAILED = "failed"
BATCH_CANCELLED = "cancelled"

BATCH_STATUSES = frozenset(
    {
        BATCH_QUEUED,
        BATCH_RUNNING,
        BATCH_COMPLETE,
        BATCH_PARTIAL_FAILED,
        BATCH_FAILED,
        BATCH_CANCELLED,
    }
)

BATCH_TERMINAL_STATUSES = frozenset(
    {BATCH_COMPLETE, BATCH_PARTIAL_FAILED, BATCH_FAILED, BATCH_CANCELLED}
)


class AnalysisBatch(Base):
    __tablename__ = "analysis_batches"
    __table_args__ = (Index("ix_analysis_batches_upload_id", "upload_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Nullable: a single-game batch is not tied to a whole upload. When it is
    # set, deleting the upload removes the batch and everything under it.
    upload_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploads.id", ondelete="CASCADE"), nullable=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False)

    # Engine identity *without* the game - see app/services/analysis_config.py.
    # Every job in a batch shares this; each job's own config_hash combines it
    # with that game's hash.
    engine_config_hash: Mapped[str] = mapped_column(String, nullable=False)
    requested_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    upload: Mapped["Upload | None"] = relationship(back_populates="analysis_batches")
    jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="AnalysisJob.created_at",
    )
