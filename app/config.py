from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    sentry_dsn: str = ""
    session_cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000, https://mpcampo.vercel.app/"
    session_idle_minutes: int = 60 * 24 * 14
    session_absolute_days: int = 90
    csrf_cookie_name: str = "csrf_token"
    session_cookie_name: str = "session_id"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()