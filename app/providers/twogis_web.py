import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.models import OrganizationReply, Review, SalonProfile, Service
from app.providers.base import PlaceProvider
from app.providers.twogis import TwoGisPlaceProvider


class TwoGisEnrichedProvider(PlaceProvider):
    """2GIS API identity enriched from its public, server-rendered card."""

    user_agent = "BeautyInspector/0.1"

    def __init__(self, api_key: str, language: str = "ru") -> None:
        self.api = TwoGisPlaceProvider(api_key, language)

    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        profile = await self.api.collect(query, city)
        base_url = f"https://2gis.ru/firm/{profile.provider_id}"
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                headers=headers,
            ) as client:
                prices_response = await client.get(f"{base_url}/tab/prices")
                reviews_response = await client.get(f"{base_url}/tab/reviews")
                prices_response.raise_for_status()
                reviews_response.raise_for_status()
                api_reviews = await self.fetch_all_reviews(
                    client,
                    profile.provider_id,
                    reviews_response.text,
                    base_url,
                )
            profile.services = self.parse_services(
                prices_response.text,
                f"{base_url}/tab/prices",
            )
            profile.reviews = api_reviews or self.parse_reviews(
                reviews_response.text
            )
            profile.map_url = str(reviews_response.url).removesuffix("/tab/reviews")
            if profile.sources:
                profile.sources[0].url = profile.map_url
        except (httpx.HTTPError, ValueError):
            # API data remains useful when the public card is temporarily unavailable.
            pass
        return profile

    @staticmethod
    def parse_services(
        html: str,
        source_url: str | None = None,
    ) -> list[Service]:
        soup = BeautifulSoup(html, "html.parser")
        services: list[Service] = []
        seen: set[tuple[str, str]] = set()
        for article in soup.find_all("article"):
            text = article.get_text(" ", strip=True)
            price_match = re.search(
                r"(?:от\s+)?\d[\d \u00a0]*(?:[–—-]\s*\d[\d \u00a0]*)?\s*₽",
                text,
                flags=re.IGNORECASE,
            )
            if not price_match:
                continue
            name_node = article.find("button")
            name = name_node.get_text(" ", strip=True) if name_node else ""
            price = " ".join(price_match.group(0).replace("\u00a0", " ").split())
            if not name or len(name) > 160:
                continue
            key = (name.casefold(), price)
            if key not in seen:
                services.append(
                    Service(
                        name=name,
                        price=price,
                        provider="2gis",
                        source_url=source_url,
                    )
                )
                seen.add(key)
        return services[:100]

    @classmethod
    async def fetch_all_reviews(
        cls,
        client: httpx.AsyncClient,
        provider_id: str,
        card_html: str,
        base_url: str,
    ) -> list[Review]:
        key_match = re.search(r'"reviewApiKey":"([^"]+)"', card_html)
        if key_match is None:
            return []
        url: str | None = (
            "https://public-api.reviews.2gis.com/2.0/branches/"
            f"{provider_id}/reviews"
        )
        params: dict[str, str | int] | None = {
            "key": key_match.group(1),
            "limit": 50,
            "offset": 0,
            "rated": "true",
            "sort_by": "date_edited",
        }
        reviews: list[Review] = []
        visited: set[str] = set()
        while url and url not in visited and len(visited) < 500:
            visited.add(url)
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                break
            reviews.extend(
                cls.parse_api_reviews(payload, provider_id, base_url)
            )
            next_link = (payload.get("meta") or {}).get("next_link")
            url = next_link if isinstance(next_link, str) else None
            params = None
        return cls._deduplicate_reviews(reviews)

    @classmethod
    def parse_api_reviews(
        cls,
        payload: dict[str, Any],
        provider_id: str,
        base_url: str,
    ) -> list[Review]:
        result: list[Review] = []
        raw_reviews = payload.get("reviews")
        if not isinstance(raw_reviews, list):
            return result
        for raw in raw_reviews:
            if not isinstance(raw, dict):
                continue
            text = " ".join(str(raw.get("text") or "").split())
            if not text or raw.get("is_hidden"):
                continue
            review_id = str(raw.get("id") or "") or None
            user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
            result.append(
                Review(
                    provider="2gis",
                    provider_review_id=review_id,
                    author=str(
                        user.get("name")
                        or user.get("first_name")
                        or "Пользователь 2ГИС"
                    ),
                    rating=raw.get("rating"),
                    text=text,
                    published_at=str(
                        raw.get("date_edited")
                        or raw.get("date_created")
                        or ""
                    )
                    or None,
                    url=(
                        f"{base_url}/tab/reviews"
                        + (f"?review={review_id}" if review_id else "")
                    ),
                    organization_replies=cls._official_answers(
                        raw.get("official_answer")
                    ),
                )
            )
        return result

    @staticmethod
    def _official_answers(value: Any) -> list[OrganizationReply]:
        raw_answers = value if isinstance(value, list) else [value]
        answers: list[OrganizationReply] = []
        for raw in raw_answers:
            if not isinstance(raw, dict) or not raw.get("text"):
                continue
            user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
            answers.append(
                OrganizationReply(
                    author=str(
                        user.get("name")
                        or raw.get("author")
                        or "Представитель организации"
                    ),
                    text=" ".join(str(raw["text"]).split()),
                    published_at=str(
                        raw.get("date_edited")
                        or raw.get("date_created")
                        or ""
                    )
                    or None,
                )
            )
        return answers

    @staticmethod
    def parse_reviews(html: str) -> list[Review]:
        soup = BeautifulSoup(html, "html.parser")
        reviews: list[Review] = []
        seen: set[str] = set()
        excluded = (
            "политик",
            "пользовательск",
            "правообладател",
            "javascript",
        )
        for anchor in soup.find_all("a"):
            text = " ".join(anchor.get_text(" ", strip=True).split())
            normalized = text.casefold()
            if (
                len(text) < 45
                or len(text) > 2000
                or any(marker in normalized for marker in excluded)
                or normalized in seen
            ):
                continue
            reviews.append(
                Review(
                    author="Пользователь 2ГИС",
                    text=text,
                    provider="2gis",
                )
            )
            seen.add(normalized)
        return reviews

    @staticmethod
    def _deduplicate_reviews(reviews: list[Review]) -> list[Review]:
        result: list[Review] = []
        seen: set[str] = set()
        for review in reviews:
            key = review.provider_review_id or (
                f"{review.author}|{review.published_at}|{review.text}".casefold()
            )
            if key not in seen:
                result.append(review)
                seen.add(key)
        return result
