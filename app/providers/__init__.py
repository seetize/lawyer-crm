from app.config import Settings
from app.providers.base import PlaceProvider
from app.providers.composite import CompositePlaceProvider
from app.providers.demo import DemoPlaceProvider
from app.providers.google import GooglePlacesProvider
from app.providers.twogis_web import TwoGisEnrichedProvider
from app.providers.yandex import YandexMapsProvider
from app.providers.yclients import YClientsEnricher


def build_provider(settings: Settings) -> PlaceProvider:
    if settings.data_provider in {"yandex", "2gis", "multi"}:
        providers: list[PlaceProvider] = []
        enrichers = []
        if settings.data_provider in {"yandex", "multi"}:
            providers.append(
                YandexMapsProvider(
                    settings.default_language,
                    max_review_pages=settings.yandex_max_review_pages,
                )
            )
        if settings.twogis_api_key:
            providers.append(
                TwoGisEnrichedProvider(
                    settings.twogis_api_key,
                    settings.default_language,
                )
            )
        if settings.data_provider == "multi" and settings.google_places_api_key:
            providers.append(
                GooglePlacesProvider(
                    settings.google_places_api_key,
                    settings.default_language,
                )
            )
        if not providers:
            raise RuntimeError("Для 2gis нужен TWOGIS_API_KEY")
        enrichers.append(
            YClientsEnricher(
                settings.yclients_partner_token,
                settings.yclients_user_token,
            )
        )
        return CompositePlaceProvider(providers, enrichers=enrichers)
    if settings.data_provider == "google":
        if not settings.google_places_api_key:
            raise RuntimeError("GOOGLE_PLACES_API_KEY обязателен для DATA_PROVIDER=google")
        return GooglePlacesProvider(settings.google_places_api_key, settings.default_language)
    return DemoPlaceProvider()
