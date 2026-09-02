from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    nvidia_api_key: str = ""
    ai_base_url: str = "https://integrate.api.nvidia.com/v1"
    sentry_dsn: str = ""
    session_cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000"
    session_idle_minutes: int = 60 * 24 * 14
    session_absolute_days: int = 90
    csrf_cookie_name: str = "csrf_token"
    session_cookie_name: str = "session_id"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()