import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.upload import MoveOut


class UploadOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    original_filename: str
    player_name: str
    uploaded_at: datetime
    game_count: int
    warnings: list[str]


class UploadListItemOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    original_filename: str
    player_name: str
    uploaded_at: datetime
    game_count: int


class PaginatedUploadsOut(BaseModel):
    items: list[UploadListItemOut]
    page: int
    page_size: int
    total: int


class GameSummaryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    game_index: int
    white_name: str
    black_name: str
    white_elo: int | None
    black_elo: int | None
    result: str
    user_color: str
    user_result: str
    eco: str | None
    opening_name: str | None
    opening_source: str | None = None
    opening_matched_ply: int | None = None
    move_count: int


class PaginatedGameSummariesOut(BaseModel):
    items: list[GameSummaryOut]
    page: int
    page_size: int
    total: int


class GameDetailOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    game_index: int
    event: str | None
    site: str | None
    round: str | None
    played_date_text: str | None
    white_name: str
    black_name: str
    white_elo: int | None
    black_elo: int | None
    result: str
    user_color: str
    user_result: str
    eco: str | None
    opening_name: str | None
    opening_source: str | None = None
    opening_matched_ply: int | None = None
    move_count: int
    moves: list[MoveOut]
