import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game


class Move(Base):
    __tablename__ = "moves"
    __table_args__ = (
        UniqueConstraint("game_id", "ply", name="uq_moves_game_id_ply"),
        Index("ix_moves_game_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    ply: Mapped[int] = mapped_column(Integer, nullable=False)
    san: Mapped[str] = mapped_column(String, nullable=False)
    uci: Mapped[str] = mapped_column(String, nullable=False)
    fen_before: Mapped[str] = mapped_column(Text, nullable=False)
    fen_after: Mapped[str] = mapped_column(Text, nullable=False)

    game: Mapped["Game"] = relationship(back_populates="moves")
