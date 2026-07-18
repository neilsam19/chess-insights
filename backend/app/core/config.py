from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    max_upload_size_bytes: int = 5 * 1024 * 1024  # 5 MB

    model_config = {"env_file": ".env"}


settings = Settings()
