from __future__ import annotations

from app.catalog.db import CatalogRepository
from app.catalog.discovery import TwoGisCityDiscovery, YandexCityDiscovery
from app.catalog.domain import CitySpec, DiscoveryScope
from app.catalog.service import CityCatalogService
from app.config import Settings
from app.providers.yandex import YandexMapsProvider
from app.providers.twogis_web import TwoGisEnrichedProvider


def build_city_spec(settings: Settings) -> CitySpec:
    west, south, east, north = settings.catalog_bbox_values()
    return CitySpec(
        name=settings.catalog_city,
        country_code=settings.catalog_country_code,
        timezone=settings.catalog_timezone,
        yandex_geo_id=settings.catalog_yandex_geo_id,
        scope=DiscoveryScope(
            west=west,
            south=south,
            east=east,
            north=north,
        ),
    )


def build_catalog_repository(settings: Settings) -> CatalogRepository:
    repository = CatalogRepository(
        settings.catalog_database_url,
        snapshot_limit=settings.catalog_snapshot_limit,
    )
    if settings.catalog_database_url.startswith("sqlite"):
        repository.create_schema()
    else:
        repository.health()
    return repository


def build_catalog_service(
    settings: Settings,
    repository: CatalogRepository | None = None,
) -> CityCatalogService:
    return CityCatalogService(
        repository or build_catalog_repository(settings),
        YandexCityDiscovery(settings.default_language),
        YandexMapsProvider(
            settings.default_language,
            max_review_pages=settings.yandex_max_review_pages,
            ranking_queries=[
                query.strip()
                for query in settings.yandex_ranking_queries.split(",")
                if query.strip()
            ],
            ranking_max_pages=settings.yandex_ranking_max_pages,
        ),
        max_pages=settings.catalog_max_pages,
        max_partition_depth=settings.catalog_max_partition_depth,
        refresh_hours=settings.catalog_refresh_hours,
    )


def build_twogis_catalog_service(
    settings: Settings,
    repository: CatalogRepository,
) -> CityCatalogService | None:
    if not settings.twogis_api_key:
        return None
    return CityCatalogService(
        repository,
        TwoGisCityDiscovery(settings.twogis_api_key, settings.default_language),
        TwoGisEnrichedProvider(settings.twogis_api_key, settings.default_language),
        max_pages=5,
        max_partition_depth=max(3, settings.catalog_max_partition_depth),
        refresh_hours=settings.catalog_refresh_hours,
    )
