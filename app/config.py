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
    catalog_database_url: str = "sqlite:///./data/sindy_catalog.db"
    catalog_city: str = "Ярославль"
    catalog_country_code: str = "RU"
    catalog_timezone: str = "Europe/Moscow"
    catalog_categories: str = "ногтевая студия,салон красоты,студия бровей и ресниц"
    catalog_bbox: str = "39.70,57.50,40.05,57.75"
    catalog_yandex_geo_id: str = "16"
    catalog_max_pages: int = 8
    catalog_max_partition_depth: int = 2
    catalog_enrich_batch_size: int = 10
    catalog_refresh_hours: int = 168
    catalog_snapshot_limit: int = 3
    telegram_admin_ids: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def catalog_category_list(self) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip()
                for value in self.catalog_categories.split(",")
                if value.strip()
            )
        )

    def catalog_bbox_values(self) -> tuple[float, float, float, float]:
        values = tuple(float(value.strip()) for value in self.catalog_bbox.split(","))
        if len(values) != 4:
            raise ValueError("CATALOG_BBOX must contain west,south,east,north")
        west, south, east, north = values
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("CATALOG_BBOX is outside valid coordinate bounds")
        return west, south, east, north

    def telegram_admin_id_set(self) -> set[int]:
        return {
            int(value.strip())
            for value in self.telegram_admin_ids.split(",")
            if value.strip().isdigit()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
