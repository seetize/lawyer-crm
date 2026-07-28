import httpx

from app.models import Review, SalonProfile, SourceRating, SourceRef
from app.providers.base import PlaceNotFoundError, PlaceProvider


class GooglePlacesProvider(PlaceProvider):
    base_url = "https://places.googleapis.com/v1"
    fields = ",".join(
        [
            "places.id",
            "places.displayName",
            "places.formattedAddress",
            "places.rating",
            "places.userRatingCount",
            "places.reviews",
            "places.priceLevel",
            "places.regularOpeningHours",
            "places.websiteUri",
            "places.googleMapsUri",
        ]
    )

    def __init__(self, api_key: str, language: str = "ru") -> None:
        self.api_key = api_key
        self.language = language

    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        headers = {
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": self.fields,
        }
        payload = {
            "textQuery": f"{query}, {city}" if city else query,
            "languageCode": self.language,
            "pageSize": 1,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/places:searchText",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        places = response.json().get("places", [])
        if not places:
            raise PlaceNotFoundError(f"Заведение не найдено: {query}")
        return self._normalize(places[0])

    @staticmethod
    def _normalize(place: dict) -> SalonProfile:
        reviews = [
            Review(
                author=item.get("authorAttribution", {}).get("displayName", "Пользователь"),
                rating=item.get("rating"),
                text=item.get("text", {}).get("text", ""),
                published_at=item.get("relativePublishTimeDescription"),
                provider="google_places",
            )
            for item in place.get("reviews", [])
            if item.get("text", {}).get("text")
        ]
        price = place.get("priceLevel")
        price_labels = {
            "PRICE_LEVEL_FREE": "бесплатно",
            "PRICE_LEVEL_INEXPENSIVE": "низкий",
            "PRICE_LEVEL_MODERATE": "средний",
            "PRICE_LEVEL_EXPENSIVE": "высокий",
            "PRICE_LEVEL_VERY_EXPENSIVE": "очень высокий",
        }
        return SalonProfile(
            provider="google_places",
            provider_id=place["id"],
            primary_provider="google_places",
            name=place.get("displayName", {}).get("text", "Без названия"),
            address=place.get("formattedAddress"),
            rating=place.get("rating"),
            reviews_count=place.get("userRatingCount"),
            reviews=reviews,
            price_level=price_labels.get(price),
            opening_hours=place.get("regularOpeningHours", {}).get(
                "weekdayDescriptions", []
            ),
            website=place.get("websiteUri"),
            map_url=place.get("googleMapsUri"),
            ratings=[
                SourceRating(
                    provider="google_places",
                    rating=place.get("rating"),
                    reviews_count=place.get("userRatingCount"),
                    url=place.get("googleMapsUri"),
                )
            ],
            sources=[
                SourceRef(
                    provider="google_places",
                    provider_id=place["id"],
                    url=place.get("googleMapsUri"),
                )
            ],
        )
