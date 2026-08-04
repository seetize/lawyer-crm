import asyncio

from app.models import SalonProfile
from app.providers.base import PlaceNotFoundError, PlaceProvider, ProfileEnricher


class CompositePlaceProvider(PlaceProvider):
    """Collects the same establishment from all configured providers."""

    def __init__(
        self,
        providers: list[PlaceProvider],
        enrichers: list[ProfileEnricher] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("Нужен хотя бы один источник данных")
        self.providers = providers
        self.enrichers = enrichers or []

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
        for enricher in self.enrichers:
            try:
                profile = await enricher.enrich(profile)
            except Exception:
                # Optional enrichment must not discard already collected map data.
                continue
        return profile

    @staticmethod
    def _merge(base: SalonProfile, extra: SalonProfile) -> SalonProfile:
        data = base.model_dump()
        for field in (
            "address",
            "description",
            "rating",
            "reviews_count",
            "price_level",
            "website",
            "booking_url",
        ):
            if data.get(field) is None:
                data[field] = getattr(extra, field)
        for field in (
            "opening_hours",
            "categories",
            "awards",
            "masters",
            "news",
            "stories",
            "features",
            "branches",
            "search_rankings",
            "available_slots",
        ):
            if not data.get(field):
                data[field] = getattr(extra, field)

        data["reviews"] = CompositePlaceProvider._merge_reviews(
            base.reviews,
            extra.reviews,
        )
        data["media"] = list(
            {
                (item.url, item.media_type): item
                for item in [*base.media, *extra.media]
            }.values()
        )
        # Yandex is the primary card and its category topology must stay exact.
        # Other map providers are a fallback only when Yandex has no public menu.
        data["services"] = base.services or extra.services

        known_ratings = {rating.provider for rating in base.ratings}
        data["ratings"] = base.ratings + [
            rating for rating in extra.ratings if rating.provider not in known_ratings
        ]
        known_sources = {
            (source.provider, source.provider_id) for source in base.sources
        }
        data["sources"] = base.sources + [
            source
            for source in extra.sources
            if (source.provider, source.provider_id) not in known_sources
        ]
        data["provider"] = "+".join(
            dict.fromkeys(source.provider for source in data["sources"])
        )
        data["source_payloads"] = {
            **base.source_payloads,
            **extra.source_payloads,
        }
        return SalonProfile.model_validate(data)

    @staticmethod
    def _merge_reviews(base: list, extra: list) -> list:
        result = list(base)
        seen = {CompositePlaceProvider._review_key(review) for review in base}
        for review in extra:
            key = CompositePlaceProvider._review_key(review)
            if key not in seen:
                result.append(review)
                seen.add(key)
        return result

    @staticmethod
    def _review_key(review) -> tuple[str, str]:
        if review.provider_review_id:
            return review.provider, review.provider_review_id
        normalized = " ".join(
            f"{review.author} {review.published_at or ''} {review.text}".casefold().split()
        )
        return review.provider, normalized
