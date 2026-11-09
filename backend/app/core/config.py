
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application configuration
    app_env: str = "development"
    app_name: str = "Vanguard AI"
    api_v1_prefix: str = "/api/v1"

    # Database configuration
    database_url: SecretStr

    # Application security
    secret_key: SecretStr

    # JWT configuration
    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"

    # Token expiration
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
