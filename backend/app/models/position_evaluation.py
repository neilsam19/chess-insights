import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.analysis_run import AnalysisRun


class PositionEvaluation(Base):
    """One engine evaluation of one position.

    Raw engine output only. Centipawn loss, accuracy, and move-quality labels
    are all derived later and must never be stored here.

    Scores are canonical White-perspective: positive centipawns and positive
    mate values favour White. Conversion to a player's perspective happens in
    the API layer.
    """

    __tablename__ = "position_evaluations"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "ply", name="uq_position_evaluations_run_ply"),
        # A position is either a centipawn assessment or a forced mate, never
        # both and never neither - malformed engine output is rejected rather
        # than stored as an ambiguous row.
        CheckConstraint(
            "(score_cp_white IS NULL) <> (mate_in_white IS NULL)",
            name="ck_position_evaluations_one_score_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    ply: Mapped[int] = mapped_column(Integer, nullable=False)

    score_cp_white: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mate_in_white: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Raw engine evidence, not a classification - Sprint 6 needs it for
    # best-move comparison without re-running every position.
    best_move_uci: Mapped[str | None] = mapped_column(String, nullable=True)

    depth_reached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nodes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    analysis_run: Mapped["AnalysisRun"] = relationship(back_populates="evaluations")
