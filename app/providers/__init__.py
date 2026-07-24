from app.config import Settings
from app.providers.base import PlaceProvider
from app.providers.composite import CompositePlaceProvider
from app.providers.demo import DemoPlaceProvider
from app.providers.google import GooglePlacesProvider
from app.providers.twogis_web import TwoGisEnrichedProvider


def build_provider(settings: Settings) -> PlaceProvider:
    if settings.data_provider in {"2gis", "multi"}:
        providers: list[PlaceProvider] = []
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
            raise RuntimeError("Для multi/2gis нужен хотя бы один API-ключ")
        return CompositePlaceProvider(providers)
    if settings.data_provider == "google":
        if not settings.google_places_api_key:
            raise RuntimeError("GOOGLE_PLACES_API_KEY обязателен для DATA_PROVIDER=google")
        return GooglePlacesProvider(settings.google_places_api_key, settings.default_language)
    return DemoPlaceProvider()
