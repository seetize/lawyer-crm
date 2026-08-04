import asyncio
import re
from difflib import SequenceMatcher

import httpx

from app.models import BranchRef, FeatureItem, SalonProfile, SourceRating, SourceRef
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
        items = [item for item in items if self.is_active(item)]
        if not items:
            raise PlaceNotFoundError(f"Заведение не найдено: {clean_query}, {city}")
        best = max(items, key=lambda item: self._match_score(clean_query, item))
        return self._normalize(best, clean_query, city)

    async def collect_by_id(self, provider_id: str) -> SalonProfile:
        fields = "items.address,items.point,items.reviews,items.schedule,items.contact_groups,items.rubrics,items.attribute_groups,items.org"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.base_url}/byid",
                params={"id": provider_id, "key": self.api_key, "locale": self.locale, "fields": fields},
            )
        response.raise_for_status()
        item = (response.json().get("result") or {}).get("items", [None])[0]
        if not isinstance(item, dict):
            raise PlaceNotFoundError(f"2ГИС ID не найден: {provider_id}")
        if not self.is_active(item):
            raise PlaceNotFoundError(
                f"Карточка 2ГИС {provider_id} закрыта или временно не работает"
            )
        profile = self._normalize(item, str(item.get("name") or provider_id), None)
        profile.branches = await self._fetch_org_branches(item)
        return profile

    async def _fetch_org_branches(
        self, item: dict
    ) -> list[BranchRef]:
        org = item.get("org") if isinstance(item.get("org"), dict) else {}
        org_id = org.get("id")
        point = item.get("point") if isinstance(item.get("point"), dict) else {}
        if not org_id or point.get("lon") is None or point.get("lat") is None:
            return self._branches(item)
        branches: list[BranchRef] = []
        fields = "items.address,items.point,items.org"
        async with httpx.AsyncClient(timeout=20) as client:
            for page in range(1, 6):
                response = await client.get(
                self.base_url,
                params={
                    "org_id": org_id,
                    "point": f"{point['lon']},{point['lat']}",
                    "radius": 40000,
                    "page": page,
                    "page_size": 10,
                    "fields": fields,
                    "key": self.api_key,
                    "locale": self.locale,
                },
            )
                response.raise_for_status()
                raw_items = (response.json().get("result") or {}).get("items") or []
                for raw in raw_items:
                    if (
                        not isinstance(raw, dict)
                        or not raw.get("id")
                        or not self.is_active(raw)
                    ):
                        continue
                    raw_point = raw.get("point") if isinstance(raw.get("point"), dict) else {}
                    branches.append(
                        BranchRef(
                            provider_id=str(raw["id"]),
                            name=str(raw.get("name") or item.get("name") or "Филиал"),
                            address=raw.get("address_name") or raw.get("full_name"),
                            latitude=raw_point.get("lat"),
                            longitude=raw_point.get("lon"),
                            url=f"https://2gis.ru/firm/{raw['id']}",
                            position=len(branches),
                        )
                    )
                if len(raw_items) < 10:
                    break
        return list({branch.provider_id: branch for branch in branches}.values())

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

    @staticmethod
    def is_active(item: dict) -> bool:
        flags = item.get("flags") if isinstance(item.get("flags"), dict) else {}
        for key, value in {**flags, **item}.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if value is True and any(
                marker in normalized_key
                for marker in ("temporarily_closed", "permanently_closed", "is_closed")
            ):
                return False
        status = str(item.get("status") or "").casefold().replace("-", "_")
        return status not in {
            "closed",
            "inactive",
            "removed",
            "temporarily_closed",
            "permanently_closed",
        }

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
            primary_provider="2gis",
            name=item.get("name") or query,
            address=address,
            rating=reviews.get("general_rating") or reviews.get("org_rating"),
            reviews_count=reviews.get("general_review_count_with_stars")
            or reviews.get("general_review_count"),
            opening_hours=TwoGisPlaceProvider._schedule(item.get("schedule", {})),
            categories=[str(value.get("name")) for value in item.get("rubrics") or [] if isinstance(value, dict) and value.get("name")],
            features=TwoGisPlaceProvider._features(item),
            branches=TwoGisPlaceProvider._branches(item),
            website=website,
            map_url=source_url,
            ratings=[
                SourceRating(
                    provider="2gis",
                    rating=reviews.get("general_rating") or reviews.get("org_rating"),
                    reviews_count=reviews.get("general_review_count_with_stars")
                    or reviews.get("general_review_count"),
                    url=source_url,
                )
            ],
            sources=[
                SourceRef(
                    provider="2gis",
                    provider_id=provider_id,
                    url=source_url,
                )
            ],
            source_payloads={"2gis": item},
        )

    @staticmethod
    def _features(item: dict) -> list[FeatureItem]:
        result: list[FeatureItem] = []
        for group in item.get("attribute_groups") or []:
            if not isinstance(group, dict):
                continue
            category = str(group.get("name") or "").strip() or None
            for attribute in group.get("attributes") or []:
                if not isinstance(attribute, dict):
                    continue
                name = str(attribute.get("name") or "").strip()
                if name:
                    result.append(FeatureItem(name=name, value=str(attribute.get("value") or attribute.get("text") or "") or None, category=category, provider="2gis"))
        return result

    @staticmethod
    def _branches(item: dict) -> list[BranchRef]:
        org = item.get("org") if isinstance(item.get("org"), dict) else {}
        result = []
        for position, branch in enumerate(org.get("branches") or []):
            if not isinstance(branch, dict) or not branch.get("id"):
                continue
            point = branch.get("point") if isinstance(branch.get("point"), dict) else {}
            result.append(BranchRef(provider_id=str(branch["id"]), name=str(branch.get("name") or item.get("name") or "Филиал"), address=branch.get("address_name"), latitude=point.get("lat"), longitude=point.get("lon"), url=f"https://2gis.ru/firm/{branch['id']}", position=position))
        return result

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
