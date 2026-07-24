import asyncio
import re
from difflib import SequenceMatcher

import httpx

from app.models import SalonProfile, SourceRef
from app.providers.base import PlaceNotFoundError, PlaceProvider


class TwoGisPlaceProvider(PlaceProvider):
    base_url = "https://catalog.api.2gis.com/3.0/items"

    def __init__(self, api_key: str, language: str = "ru") -> None:
        self.api_key = api_key
        self.locale = "ru_RU" if language == "ru" else "en_RU"

    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        clean_query = self._remove_city(query, city)
        variants = self._query_variants(clean_query)
        async with httpx.AsyncClient(timeout=20) as client:
            responses = await asyncio.gather(
                *(
                    client.get(
                        self.base_url,
                        params=self._params(variant, city),
                    )
                    for variant in variants
                )
            )
        items_by_id: dict[str, dict] = {}
        for response in responses:
            response.raise_for_status()
            for item in response.json().get("result", {}).get("items", []):
                items_by_id[item["id"]] = item
        items = list(items_by_id.values())
        if not items:
            raise PlaceNotFoundError(f"Заведение не найдено: {clean_query}, {city}")
        best = max(items, key=lambda item: self._match_score(clean_query, item))
        return self._normalize(best, clean_query, city)

    def _params(self, query: str, city: str | None) -> dict:
        return {
            "q": " ".join(part for part in (query, city) if part),
            "key": self.api_key,
            "locale": self.locale,
            "page_size": 10,
            "fields": ",".join(
                (
                    "items.address",
                    "items.point",
                    "items.reviews",
                    "items.schedule",
                    "items.contact_groups",
                )
            ),
        }

    @staticmethod
    def _remove_city(query: str, city: str | None) -> str:
        value = " ".join(query.strip().split())
        if city:
            value = re.sub(
                rf"(?:[\s,]+){re.escape(city)}\s*$",
                "",
                value,
                flags=re.IGNORECASE,
            )
        return value.strip(" ,-")

    @staticmethod
    def _query_variants(query: str) -> list[str]:
        plain = re.sub(r"[\s_-]+", " ", query).strip()
        hyphenated = plain.replace(" ", "-")
        return list(dict.fromkeys((query.strip(), plain, hyphenated)))

    @staticmethod
    def _normalized(value: str) -> str:
        value = value.split(",", 1)[0].casefold().replace("ё", "е")
        return re.sub(r"[^a-zа-я0-9]+", "", value)

    @classmethod
    def _match_score(cls, query: str, item: dict) -> float:
        expected = cls._normalized(query)
        actual = cls._normalized(item.get("name", ""))
        if not expected or not actual:
            return 0
        exact_bonus = 2.0 if expected == actual else 0.0
        contains_bonus = 0.5 if expected in actual or actual in expected else 0.0
        return exact_bonus + contains_bonus + SequenceMatcher(
            None, expected, actual
        ).ratio()

    @staticmethod
    def _normalize(item: dict, query: str, city: str | None) -> SalonProfile:
        reviews = item.get("reviews", {})
        provider_id = item["id"]
        source_url = f"https://2gis.ru/firm/{provider_id}"
        website = TwoGisPlaceProvider._website(item.get("contact_groups", []))
        address = item.get("address_name")
        if address and city and city.casefold() not in address.casefold():
            address = f"{address}, {city}"

        return SalonProfile(
            provider="2gis",
            provider_id=provider_id,
            name=item.get("name") or query,
            address=address,
            rating=reviews.get("general_rating") or reviews.get("org_rating"),
            reviews_count=reviews.get("general_review_count_with_stars")
            or reviews.get("general_review_count"),
            opening_hours=TwoGisPlaceProvider._schedule(item.get("schedule", {})),
            website=website,
            map_url=source_url,
            sources=[SourceRef(provider="2gis", url=source_url)],
        )

    @staticmethod
    def _website(groups: list[dict]) -> str | None:
        for group in groups:
            for contact in group.get("contacts", []):
                if contact.get("type") in {"website", "url"}:
                    return contact.get("url") or contact.get("value")
        return None

    @staticmethod
    def _schedule(schedule: dict) -> list[str]:
        labels = {
            "Mon": "Пн",
            "Tue": "Вт",
            "Wed": "Ср",
            "Thu": "Чт",
            "Fri": "Пт",
            "Sat": "Сб",
            "Sun": "Вс",
        }
        result = []
        for day, label in labels.items():
            periods = schedule.get(day, {}).get("working_hours", [])
            hours = ", ".join(
                f"{period.get('from', '?')}–{period.get('to', '?')}"
                for period in periods
            )
            if hours:
                result.append(f"{label}: {hours}")
        return result
