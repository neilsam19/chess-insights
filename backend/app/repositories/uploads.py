import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.upload import Upload


def find_by_file_and_player(
    db: Session, file_sha256: str, normalized_player_name: str
) -> Upload | None:
    stmt = select(Upload).where(
        Upload.file_sha256 == file_sha256,
        Upload.normalized_player_name == normalized_player_name,
    )
    return db.execute(stmt).scalar_one_or_none()


def create_upload(
    db: Session,
    *,
    original_filename: str,
    file_sha256: str,
    player_name: str,
    normalized_player_name: str,
    game_count: int,
    warnings: list[str],
) -> Upload:
    upload = Upload(
        original_filename=original_filename,
        file_sha256=file_sha256,
        player_name=player_name,
        normalized_player_name=normalized_player_name,
        uploaded_at=datetime.now(timezone.utc),
        game_count=game_count,
        warnings=warnings,
    )
    db.add(upload)
    db.flush()  # populate upload.id for the games that reference it, without committing
    return upload


def list_uploads(db: Session, *, page: int, page_size: int) -> tuple[list[Upload], int]:
    total = db.scalar(select(func.count()).select_from(Upload)) or 0
    stmt = (
        select(Upload)
        .order_by(Upload.uploaded_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(db.execute(stmt).scalars())
    return items, total


def get_upload(db: Session, upload_id: uuid.UUID) -> Upload | None:
    return db.get(Upload, upload_id)


def delete_upload(db: Session, upload: Upload) -> None:
    db.delete(upload)
