from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str | None = None
    google_places_api_key: str | None = None
    twogis_api_key: str | None = None
    data_provider: Literal["demo", "google", "2gis", "multi"] = "demo"
    default_language: str = "ru"
    default_city: str = "Астрахань"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
