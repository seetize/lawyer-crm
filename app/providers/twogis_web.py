import re

import httpx
from bs4 import BeautifulSoup

from app.models import Review, SalonProfile, Service
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
            profile.services = self.parse_services(prices_response.text)
            profile.reviews = self.parse_reviews(reviews_response.text)
            profile.map_url = str(reviews_response.url).removesuffix("/tab/reviews")
            if profile.sources:
                profile.sources[0].url = profile.map_url
        except (httpx.HTTPError, ValueError):
            # API data remains useful when the public card is temporarily unavailable.
            pass
        return profile

    @staticmethod
    def parse_services(html: str) -> list[Service]:
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
                services.append(Service(name=name, price=price))
                seen.add(key)
        return services[:100]

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
                )
            )
            seen.add(normalized)
        return reviews[:20]

