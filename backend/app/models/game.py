import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.move import Move
    from app.models.upload import Upload


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (Index("ix_games_upload_id", "upload_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False
    )
    game_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str | None] = mapped_column(String, nullable=True)
    site: Mapped[str | None] = mapped_column(String, nullable=True)
    round: Mapped[str | None] = mapped_column(String, nullable=True)
    played_date_text: Mapped[str | None] = mapped_column(String, nullable=True)
    white_name: Mapped[str] = mapped_column(String, nullable=False)
    black_name: Mapped[str] = mapped_column(String, nullable=False)
    white_elo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    black_elo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String, nullable=False)
    user_color: Mapped[str] = mapped_column(String, nullable=False)
    user_result: Mapped[str] = mapped_column(String, nullable=False)
    # Canonical opening classification. eco/opening_name hold the *classified*
    # values, not the PGN's headers - those survive verbatim inside raw_pgn.
    # All nullable: rows imported before Sprint 3 carry NULL until backfilled.
    eco: Mapped[str | None] = mapped_column(String, nullable=True)
    opening_name: Mapped[str | None] = mapped_column(String, nullable=True)
    opening_source: Mapped[str | None] = mapped_column(String, nullable=True)
    opening_matched_ply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opening_dataset_version: Mapped[str | None] = mapped_column(String, nullable=True)
    move_count: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_pgn: Mapped[str] = mapped_column(Text, nullable=False)
    game_hash: Mapped[str] = mapped_column(String, nullable=False)

    upload: Mapped["Upload"] = relationship(back_populates="games")
    moves: Mapped[list["Move"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        order_by="Move.ply",
    )
