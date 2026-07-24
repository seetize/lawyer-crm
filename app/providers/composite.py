import asyncio

from app.models import SalonProfile
from app.providers.base import PlaceNotFoundError, PlaceProvider


class CompositePlaceProvider(PlaceProvider):
    """Collects the same establishment from all configured providers."""

    def __init__(self, providers: list[PlaceProvider]) -> None:
        if not providers:
            raise ValueError("Нужен хотя бы один источник данных")
        self.providers = providers

    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        results = await asyncio.gather(
            *(provider.collect(query, city) for provider in self.providers),
            return_exceptions=True,
        )
        profiles = [result for result in results if isinstance(result, SalonProfile)]
        if not profiles:
            errors = [str(result) for result in results if isinstance(result, Exception)]
            raise PlaceNotFoundError("; ".join(errors) or f"Не найдено: {query}")
        profile = profiles[0]
        for extra in profiles[1:]:
            profile = self._merge(profile, extra)
        return profile

    @staticmethod
    def _merge(base: SalonProfile, extra: SalonProfile) -> SalonProfile:
        data = base.model_dump()
        for field in ("address", "rating", "reviews_count", "price_level", "website"):
            if data.get(field) is None:
                data[field] = getattr(extra, field)
        for field in (
            "reviews",
            "opening_hours",
            "services",
            "masters",
            "available_slots",
        ):
            if not data.get(field):
                data[field] = getattr(extra, field)
        known_sources = {source.provider for source in base.sources}
        data["sources"] = base.sources + [
            source for source in extra.sources if source.provider not in known_sources
        ]
        data["provider"] = "+".join(source.provider for source in data["sources"])
        return SalonProfile.model_validate(data)

