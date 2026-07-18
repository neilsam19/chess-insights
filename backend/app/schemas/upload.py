from pydantic import BaseModel


class MoveOut(BaseModel):
    ply: int
    san: str
    uci: str
    fen_before: str
    fen_after: str


class GameOut(BaseModel):
    index: int
    white: str
    black: str
    white_elo: int | None = None
    black_elo: int | None = None
    date: str | None = None
    event: str | None = None
    result: str
    eco: str | None = None
    opening: str | None = None
    player_color: str
    player_result: str
    moves: list[MoveOut]


class OpeningStatOut(BaseModel):
    name: str
    games: int
    wins: int
    draws: int
    losses: int
    score_percentage: float


class UploadStatsOut(BaseModel):
    total_games: int
    wins: int
    draws: int
    losses: int
    unknown_results: int
    win_percentage: float
    score_percentage: float
    games_as_white: int
    games_as_black: int
    openings: list[OpeningStatOut]


class UploadPreviewResponse(BaseModel):
    games: list[GameOut]
    stats: UploadStatsOut
    warnings: list[str]
