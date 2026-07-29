import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.position_evaluation import PositionEvaluation

# Persisted lifecycle states. Plain strings rather than a database enum, to
# match how user_color / user_result / opening_source are already stored.
# "not_analyzed" is deliberately absent - it is the derived API state for a
# game with no run at all, never a stored value.
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"
# A run stopped by the user. Like a failed run, it holds zero evaluations -
# partial analysis is never persisted.
STATUS_CANCELLED = "cancelled"


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_game_id_created_at", "game_id", "created_at"),
        Index("ix_analysis_runs_game_id_status", "game_id", "status"),
        # Finding a job's latest attempt. There is deliberately no
        # `latest_run_id` column on analysis_jobs: a denormalized pointer can
        # drift from the run history, and this index makes deriving it cheap.
        Index("ix_analysis_runs_job_id_attempt", "analysis_job_id", "attempt_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)

    # Which queued attempt produced this run. Both nullable: runs created by
    # Sprint 4's synchronous path predate jobs entirely, and SET NULL keeps
    # their history readable if a job row is ever removed.
    analysis_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Engine provenance: evaluations are only comparable within the same
    # engine build and settings, so this is recorded per run.
    engine_name: Mapped[str] = mapped_column(String, nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String, nullable=True)
    engine_binary_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    game: Mapped["Game"] = relationship(back_populates="analysis_runs")
    evaluations: Mapped[list["PositionEvaluation"]] = relationship(
        back_populates="analysis_run",
        cascade="all, delete-orphan",
        order_by="PositionEvaluation.ply",
    )
