import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.game import Game


def list_game_summaries(
    db: Session, upload_id: uuid.UUID, *, page: int, page_size: int
) -> tuple[list[Game], int]:
    total = (
        db.scalar(select(func.count()).select_from(Game).where(Game.upload_id == upload_id))
        or 0
    )
    stmt = (
        select(Game)
        .where(Game.upload_id == upload_id)
        .order_by(Game.game_index)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(db.execute(stmt).scalars())
    return items, total


def get_game_with_moves(db: Session, game_id: uuid.UUID) -> Game | None:
    stmt = select(Game).where(Game.id == game_id).options(selectinload(Game.moves))
    return db.execute(stmt).scalar_one_or_none()


def list_all_games(db: Session, upload_id: uuid.UUID) -> list[Game]:
    """All games for an upload, unpaginated - used to compute aggregate stats."""
    stmt = select(Game).where(Game.upload_id == upload_id).order_by(Game.game_index)
    return list(db.execute(stmt).scalars())


def list_all_games_with_moves(db: Session, upload_id: uuid.UUID) -> list[Game]:
    """As `list_all_games`, plus every game's moves in one extra query.

    `selectinload` rather than lazy access: move-quality aggregation reads the
    moves of every game in the upload, and letting the relationship load per
    game would turn one request into one query per game.
    """
    stmt = (
        select(Game)
        .where(Game.upload_id == upload_id)
        .order_by(Game.game_index)
        .options(selectinload(Game.moves))
    )
    return list(db.execute(stmt).scalars())
