import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import Game


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        UniqueConstraint("file_sha256", "normalized_player_name", name="uq_uploads_file_player"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String, nullable=False)
    player_name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_player_name: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    game_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    games: Mapped[list["Game"]] = relationship(
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="Game.game_index",
    )
