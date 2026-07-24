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
        # missing_fields — контракт для будущего мульти-источника: он сможет
        # целенаправленно искать прайс, отзывы или рейтинг при повторной попытке.
        collected = await self.provider.collect(query, city)
        if previous is None:
            return collected
        return self._merge(previous, collected)

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
            "available_slots",
            "sources",
        ):
            if not update.get(field):
                update[field] = getattr(previous, field)
        return SalonProfile.model_validate(update)
