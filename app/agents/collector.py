from app.models import SalonProfile
from app.providers.base import PlaceProvider


class CollectorAgent:
    """Агент 1: собирает и нормализует факты из подключённых источников."""

    def __init__(self, provider: PlaceProvider) -> None:
        self.provider = provider

    async def run(
        self,
        query: str,
        city: str | None = None,
        missing_fields: list[str] | None = None,
        previous: SalonProfile | None = None,
    ) -> SalonProfile:
        if previous is None:
            return await self.provider.collect(query, city)
        if not missing_fields:
            return previous
        targeted = await self.provider.recollect_missing(
            query,
            city,
            missing_fields,
            previous,
        )
        if targeted is None:
            return previous
        return self._merge(previous, targeted)

    @staticmethod
    def _merge(previous: SalonProfile, current: SalonProfile) -> SalonProfile:
        update = current.model_dump()
        for field in (
            "address",
            "rating",
            "reviews_count",
            "price_level",
            "website",
            "map_url",
        ):
            if update.get(field) is None:
                update[field] = getattr(previous, field)
        for field in (
            "reviews",
            "opening_hours",
            "services",
            "masters",
            "categories",
            "awards",
            "news",
            "search_rankings",
            "available_slots",
            "sources",
        ):
            if not update.get(field):
                update[field] = getattr(previous, field)
        return SalonProfile.model_validate(update)
