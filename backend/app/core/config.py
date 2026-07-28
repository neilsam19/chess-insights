from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    max_upload_size_bytes: int = 5 * 1024 * 1024  # 5 MB
    database_url: str = "postgresql+psycopg://chess_user:chess_password@localhost:5433/chess_insights"

    # Stockfish is a binary dependency, installed manually and pointed at by
    # configuration - never downloaded at runtime and never supplied by a
    # client. Unset means engine analysis is unavailable, not that it should
    # be searched for on PATH.
    stockfish_path: str | None = None
    stockfish_depth: int = 12
    stockfish_threads: int = 1
    stockfish_hash_mb: int = 64

    model_config = {"env_file": ".env"}


settings = Settings()
