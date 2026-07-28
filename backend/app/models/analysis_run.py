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


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_game_id_created_at", "game_id", "created_at"),
        Index("ix_analysis_runs_game_id_status", "game_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)

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
