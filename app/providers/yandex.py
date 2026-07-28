import asyncio
import json
import math
import re
import urllib.parse
from difflib import SequenceMatcher
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, RequestsError

from app.models import (
    OrganizationReply,
    Review,
    SalonProfile,
    Service,
    SourceRating,
    SourceRef,
)
from app.providers.base import PlaceNotFoundError, PlaceProvider


class YandexMapsProvider(PlaceProvider):
    """Yandex-first provider based on publicly rendered Yandex Maps cards.

    The documented Organization Search API does not expose reviews. Public card
    pages currently expose up to 50 reviews per page and at most the latest 600.
    If Yandex temporarily blocks a subpage, the main card remains usable and
    other providers can still enrich the profile.
    """

    search_url = "https://yandex.ru/maps/"
    card_base_url = "https://yandex.ru/maps/org"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        language: str = "ru",
        max_review_pages: int = 12,
        review_concurrency: int = 3,
    ) -> None:
        self.language = language
        self.max_review_pages = max(1, min(max_review_pages, 12))
        self.review_concurrency = max(1, min(review_concurrency, 5))

    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
        timeout = httpx.Timeout(30, connect=15)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(
                self.search_url,
                params={"text": " ".join(part for part in (query, city) if part)},
            )
            response.raise_for_status()
            organizations = self.parse_organizations(response.text)
            if not organizations:
                raise PlaceNotFoundError(
                    f"Заведение не найдено в Яндекс Картах: {query}, {city or ''}"
                )
            item = max(
                organizations,
                key=lambda candidate: self._match_score(query, candidate),
            )
            profile = self.normalize_organization(item)
            reviews, services = await asyncio.gather(
                self._fetch_all_reviews(client, item),
                self._fetch_prices(client, item),
            )
            profile.reviews = reviews
            profile.services = services
            return profile

    @classmethod
    def parse_organizations(cls, html: str) -> list[dict[str, Any]]:
        organizations: dict[str, dict[str, Any]] = {}
        for payload in cls._state_payloads(html):
            for item in cls._walk(payload):
                provider_id = str(item.get("id", ""))
                if (
                    provider_id.isdigit()
                    and (item.get("title") or item.get("name"))
                    and (item.get("fullAddress") or item.get("address"))
                    and isinstance(item.get("ratingData"), dict)
                ):
                    organizations[provider_id] = item
        return list(organizations.values())

    @classmethod
    def normalize_organization(cls, item: dict[str, Any]) -> SalonProfile:
        provider_id = str(item["id"])
        source_url = f"{cls.card_base_url}/{provider_id}/"
        rating_data = item.get("ratingData") or {}
        rating = rating_data.get("ratingValue")
        review_count = rating_data.get("reviewCount")
        booking_url = cls._booking_url(item.get("businessLinks", []))
        description = cls._description(item)
        return SalonProfile(
            provider="yandex_maps",
            provider_id=provider_id,
            primary_provider="yandex_maps",
            name=item.get("title") or item.get("name"),
            address=item.get("fullAddress") or item.get("address"),
            description=description,
            rating=rating,
            reviews_count=review_count,
            ratings=[
                SourceRating(
                    provider="yandex_maps",
                    rating=rating,
                    reviews_count=review_count,
                    url=source_url,
                )
            ],
            opening_hours=cls._working_hours(item),
            website=cls._website(item),
            map_url=source_url,
            booking_url=booking_url,
            sources=[
                SourceRef(
                    provider="yandex_maps",
                    provider_id=provider_id,
                    url=source_url,
                )
            ],
        )

    async def _fetch_all_reviews(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
    ) -> list[Review]:
        api_reviews = await self._fetch_reviews_api(client, item)
        if api_reviews is not None:
            return self._deduplicate_reviews(api_reviews)
        return await self._fetch_reviews_from_pages(client, item)

    async def _fetch_reviews_api(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
    ) -> list[Review] | None:
        """Load every review page exposed by the Maps web client.

        Yandex first responds with a short-lived CSRF token. The browser signs
        the complete, alphabetically sorted query with a small 32-bit hash and
        retries it. Reproducing that protocol avoids loading and parsing up to
        twelve large HTML documents.
        """
        provider_id = str(item["id"])
        slug = self._slug(item.get("seoname") or item.get("title") or "organization")
        referer = f"https://yandex.ru/maps/org/{slug}/{provider_id}/reviews/"
        base_params = {
            "ajax": "1",
            "businessId": provider_id,
            "locale": "ru_RU",
            "page": "1",
            "pageSize": "50",
            "ranking": "by_relevance_org",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "X-Retpath-Y": referer,
        }
        api_url = "https://yandex.ru/maps/api/business/fetchReviews"
        try:
            browser_client = AsyncSession(
                impersonate="chrome",
                headers=headers,
                timeout=30,
                max_clients=self.review_concurrency,
            )
            bootstrap = await browser_client.get(api_url, params=base_params)
            if bootstrap.status_code in {403, 429}:
                await browser_client.close()
                return None
            bootstrap.raise_for_status()
            bootstrap_payload = bootstrap.json()
            csrf_token = bootstrap_payload.get("csrfToken")
            if not csrf_token:
                await browser_client.close()
                return self._reviews_from_api_payload(
                    bootstrap_payload,
                    provider_id,
                )
        except (RequestsError, ValueError):
            return None

        review_count = int((item.get("ratingData") or {}).get("reviewCount") or 0)
        total_pages = max(1, math.ceil(review_count / 50))
        total_pages = min(total_pages, self.max_review_pages)
        semaphore = asyncio.Semaphore(self.review_concurrency)

        async def fetch(page: int) -> list[Review] | None:
            params = {
                **base_params,
                "csrfToken": str(csrf_token),
                "page": str(page),
            }
            params["s"] = self._query_signature(params)
            async with semaphore:
                try:
                    response = await browser_client.get(api_url, params=params)
                    if response.status_code in {403, 429}:
                        return None
                    response.raise_for_status()
                    return self._reviews_from_api_payload(
                        response.json(),
                        provider_id,
                    )
                except (RequestsError, ValueError):
                    return None

        try:
            pages = await asyncio.gather(
                *(fetch(page) for page in range(1, total_pages + 1))
            )
        finally:
            await browser_client.close()
        if not pages or pages[0] is None:
            return None
        reviews: list[Review] = []
        for page_reviews in pages:
            if page_reviews:
                reviews.extend(page_reviews)
        return reviews

    @classmethod
    def _reviews_from_api_payload(
        cls,
        payload: dict[str, Any],
        provider_id: str,
    ) -> list[Review] | None:
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
            return None
        return [
            review
            for raw in data["reviews"]
            if isinstance(raw, dict)
            and (review := cls._normalize_review(raw, provider_id))
        ]

    @staticmethod
    def _query_signature(params: dict[str, str]) -> str:
        query = urllib.parse.urlencode(
            sorted(params.items(), key=lambda pair: pair[0].casefold())
        )
        value = 5381
        for character in query:
            value = ((33 * value) ^ ord(character)) & 0xFFFFFFFF
        return str(value)

    async def _fetch_reviews_from_pages(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
    ) -> list[Review]:
        provider_id = str(item["id"])
        slug = self._slug(item.get("seoname") or item.get("title") or "organization")
        first = await self._fetch_review_page(client, provider_id, slug, 1)
        if first is None:
            return []
        reviews, total_pages = first
        total_pages = min(total_pages, self.max_review_pages)
        if total_pages <= 1:
            return self._deduplicate_reviews(reviews)

        semaphore = asyncio.Semaphore(self.review_concurrency)

        async def fetch(page: int) -> list[Review]:
            async with semaphore:
                result = await self._fetch_review_page(
                    client,
                    provider_id,
                    slug,
                    page,
                )
                return result[0] if result else []

        pages = await asyncio.gather(
            *(fetch(page) for page in range(2, total_pages + 1))
        )
        for page_reviews in pages:
            reviews.extend(page_reviews)
        return self._deduplicate_reviews(reviews)

    async def _fetch_review_page(
        self,
        client: httpx.AsyncClient,
        provider_id: str,
        slug: str,
        page: int,
    ) -> tuple[list[Review], int] | None:
        urls = (
            f"https://yandex.ru/maps/org/{slug}/{provider_id}/reviews/",
            f"https://yandex.com/maps/org/{slug}/{provider_id}/reviews/",
        )
        for url in urls:
            try:
                response = await client.get(
                    url,
                    params={"page": page},
                    headers={"Referer": f"{self.search_url}"},
                )
                if response.status_code in {403, 429}:
                    continue
                response.raise_for_status()
                parsed = self.parse_review_page(response.text, provider_id)
                if parsed is not None:
                    return parsed
            except httpx.HTTPError:
                continue
        return None

    @classmethod
    def parse_review_page(
        cls,
        html: str,
        provider_id: str,
    ) -> tuple[list[Review], int] | None:
        for payload in cls._state_payloads(html):
            for item in cls._walk(payload):
                raw_reviews = item.get("reviews")
                params = item.get("params")
                if not isinstance(raw_reviews, list) or not isinstance(params, dict):
                    continue
                if raw_reviews and not isinstance(raw_reviews[0], dict):
                    continue
                total_pages = int(params.get("totalPages") or 1)
                return (
                    [
                        review
                        for raw in raw_reviews
                        if (review := cls._normalize_review(raw, provider_id))
                    ],
                    total_pages,
                )
        return None

    @classmethod
    def _normalize_review(
        cls,
        raw: dict[str, Any],
        provider_id: str,
    ) -> Review | None:
        text = " ".join(str(raw.get("text") or "").split())
        if not text:
            return None
        author_data = raw.get("author") if isinstance(raw.get("author"), dict) else {}
        author = (
            author_data.get("name")
            or author_data.get("publicName")
            or raw.get("authorName")
            or "Пользователь Яндекс Карт"
        )
        review_id = str(raw.get("reviewId") or raw.get("id") or "") or None
        replies: list[OrganizationReply] = []
        business_comment = raw.get("businessComment")
        if isinstance(business_comment, dict) and business_comment.get("text"):
            replies.append(
                OrganizationReply(
                    author="Представитель организации",
                    text=" ".join(str(business_comment["text"]).split()),
                    published_at=cls._date_value(
                        business_comment.get("updatedTime")
                        or business_comment.get("createdTime")
                    ),
                )
            )
        review_url = (
            f"https://yandex.ru/maps/org/{provider_id}/reviews/"
            + (f"?reviewId={review_id}" if review_id else "")
        )
        return Review(
            provider="yandex_maps",
            provider_review_id=review_id,
            author=str(author),
            rating=raw.get("rating"),
            text=text,
            published_at=cls._date_value(
                raw.get("updatedTime") or raw.get("createdTime")
            ),
            url=review_url,
            organization_replies=replies,
        )

    async def _fetch_prices(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
    ) -> list[Service]:
        provider_id = str(item["id"])
        slug = self._slug(item.get("seoname") or item.get("title") or "organization")
        try:
            async with AsyncSession(
                impersonate="chrome",
                timeout=30,
            ) as browser_client:
                response = await browser_client.get(
                    f"https://yandex.ru/maps/org/{slug}/{provider_id}/prices/"
                )
                if response.status_code not in {403, 404, 429}:
                    response.raise_for_status()
                    services = self.parse_prices(
                        response.text,
                        str(response.url),
                    )
                    if services:
                        return services
        except (RequestsError, ValueError):
            pass
        for host in ("yandex.ru", "yandex.com"):
            try:
                response = await client.get(
                    f"https://{host}/maps/org/{slug}/{provider_id}/prices/"
                )
                if response.status_code in {403, 404, 429}:
                    continue
                response.raise_for_status()
                services = self.parse_prices(response.text, str(response.url))
                if services:
                    return services
            except httpx.HTTPError:
                continue
        return []

    @classmethod
    def parse_prices(cls, html: str, source_url: str) -> list[Service]:
        result: list[Service] = []
        seen: set[tuple[str, str]] = set()
        for payload in cls._state_payloads(html):
            for item in cls._walk(payload):
                category_items = item.get("categoryItems")
                if not isinstance(category_items, list):
                    continue
                for raw in category_items:
                    if not isinstance(raw, dict):
                        continue
                    name = " ".join(
                        str(raw.get("title") or raw.get("name") or "").split()
                    )
                    price = cls._price_text(raw)
                    if not name:
                        continue
                    key = (name.casefold(), price or "")
                    if key in seen:
                        continue
                    result.append(
                        Service(
                            name=name,
                            price=price,
                            duration=raw.get("description"),
                            provider="yandex_maps",
                            source_url=source_url,
                        )
                    )
                    seen.add(key)
        return result

    @staticmethod
    def _price_text(raw: dict[str, Any]) -> str | None:
        price = raw.get("price")
        currency = raw.get("currency") or "₽"
        if isinstance(price, dict):
            value = price.get("value") or price.get("text")
            currency = price.get("currency") or currency
        else:
            value = price
        if value in (None, ""):
            return None
        text = str(value)
        if currency and str(currency) not in text:
            text = f"{text} {currency}"
        return text

    @staticmethod
    def _state_payloads(html: str) -> Iterable[Any]:
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", attrs={"type": "application/json"}):
            try:
                yield json.loads(script.string or "")
            except json.JSONDecodeError:
                continue

    @classmethod
    def _walk(cls, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from cls._walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from cls._walk(item)

    @staticmethod
    def _description(item: dict[str, Any]) -> str | None:
        for key in ("shortDescription", "businessDescription", "about"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        categories = [
            category.get("name")
            for category in item.get("categories", [])
            if isinstance(category, dict) and category.get("name")
        ]
        return ", ".join(categories) or None

    @staticmethod
    def _booking_url(links: list[dict[str, Any]]) -> str | None:
        for link in links:
            if link.get("type") == "booking" and link.get("href"):
                return link["href"]
        return None

    @staticmethod
    def _website(item: dict[str, Any]) -> str | None:
        for key in ("site", "website"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for link in item.get("businessLinks", []):
            if link.get("type") in {"site", "website"} and link.get("href"):
                return link["href"]
        return None

    @staticmethod
    def _working_hours(item: dict[str, Any]) -> list[str]:
        raw = item.get("workingTime")
        if not isinstance(raw, list):
            text = item.get("workingTimeText")
            return [text] if isinstance(text, str) and text else []
        labels = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        result: list[str] = []
        for label, periods in zip(labels, raw, strict=False):
            rendered: list[str] = []
            for period in periods or []:
                start = YandexMapsProvider._clock(period.get("from", {}))
                end = YandexMapsProvider._clock(period.get("to", {}))
                if start and end:
                    rendered.append(f"{start}–{end}")
            result.append(f"{label}: {', '.join(rendered) if rendered else 'выходной'}")
        return result

    @staticmethod
    def _clock(value: dict[str, Any]) -> str | None:
        if not isinstance(value, dict) or "hours" not in value:
            return None
        return f"{int(value['hours']):02d}:{int(value.get('minutes', 0)):02d}"

    @staticmethod
    def _date_value(value: Any) -> str | None:
        return str(value) if value not in (None, "") else None

    @staticmethod
    def _slug(value: str) -> str:
        value = value.casefold().replace("ё", "е")
        value = re.sub(r"[^a-zа-я0-9]+", "_", value)
        return value.strip("_") or "organization"

    @staticmethod
    def _normalized(value: str) -> str:
        value = value.casefold().replace("ё", "е")
        return re.sub(r"[^a-zа-я0-9]+", "", value)

    @classmethod
    def _match_score(cls, query: str, item: dict[str, Any]) -> float:
        expected = cls._normalized(query)
        actual = cls._normalized(item.get("title") or item.get("name") or "")
        if not expected or not actual:
            return 0
        exact = 2.0 if expected == actual else 0.0
        contains = 0.5 if expected in actual or actual in expected else 0.0
        return exact + contains + SequenceMatcher(None, expected, actual).ratio()

    @staticmethod
    def _deduplicate_reviews(reviews: list[Review]) -> list[Review]:
        result: list[Review] = []
        seen: set[str] = set()
        for review in reviews:
            key = review.provider_review_id or " ".join(
                f"{review.author} {review.published_at or ''} {review.text}"
                .casefold()
                .split()
            )
            if key not in seen:
                result.append(review)
                seen.add(key)
        return result
