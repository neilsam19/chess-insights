from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    max_upload_size_bytes: int = 5 * 1024 * 1024  # 5 MB
    database_url: str = "postgresql+psycopg://chess_user:chess_password@localhost:5433/chess_insights"

    model_config = {"env_file": ".env"}


settings = Settings()
