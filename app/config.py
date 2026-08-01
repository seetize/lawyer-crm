from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str | None = None
    google_places_api_key: str | None = None
    twogis_api_key: str | None = None
    yandex_maps_api_key: str | None = None
    yclients_partner_token: str | None = None
    yclients_user_token: str | None = None
    openai_api_key: str | None = None
    openai_reviews_enabled: bool = True
    openai_review_model: str = "gpt-5.6-luna"
    review_summary_provider: Literal["local", "openai", "off"] = "local"
    review_summary_max_chars: int = 700
    yandex_max_review_pages: int = 12
    yandex_ranking_queries: str = "ногтевая студия,салон красоты"
    yandex_ranking_max_pages: int = 20
    data_provider: Literal["demo", "google", "2gis", "yandex", "multi"] = "demo"
    default_language: str = "ru"
    default_city: str = "Астрахань"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
