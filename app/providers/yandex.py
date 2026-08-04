import asyncio
import json
import math
import re
import urllib.parse
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, RequestsError

from app.models import (
    BranchRef,
    FeatureItem,
    MediaItem,
    NewsItem,
    OrganizationReply,
    Review,
    SalonProfile,
    SearchRanking,
    Service,
    SourceRating,
    SourceRef,
    StoryItem,
)
from app.providers.base import PlaceNotFoundError, PlaceProvider
from app.curl_runtime import curl_ca_bundle


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
        ranking_queries: list[str] | None = None,
        ranking_max_pages: int = 20,
    ) -> None:
        self.language = language
        self.max_review_pages = max(1, min(max_review_pages, 12))
        self.review_concurrency = max(1, min(review_concurrency, 5))
        self.ranking_queries = list(
            dict.fromkeys(
                " ".join(query.split())
                for query in (ranking_queries or [])
                if query.strip()
            )
        )
        self.ranking_max_pages = max(1, min(ranking_max_pages, 20))

    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
        html = await self._browser_html(
            self.search_url,
            {"text": " ".join(part for part in (query, city) if part)},
        )
        organizations = self.parse_organizations(html)
        if not organizations:
            raise PlaceNotFoundError(
                f"Заведение не найдено в Яндекс Картах: {query}, {city or ''}"
            )
        item = max(
            organizations,
            key=lambda candidate: self._match_score(query, candidate),
        )
        profile = self.normalize_organization(item)
        timeout = httpx.Timeout(30, connect=15)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            reviews, services, news, rankings, branches = await asyncio.gather(
                self._fetch_all_reviews(client, item),
                self._fetch_prices(client, item),
                self._fetch_news(item),
                self._fetch_search_rankings(item, city),
                self._fetch_branches(item, city),
            )
            profile.reviews = reviews
            self._set_review_coverage(profile, item)
            profile.services = services
            profile.news = news
            profile.stories = self._merge_stories(
                profile.stories, self.parse_stories_html(html)
            )
            profile.branches = branches or profile.branches
            profile.search_rankings = rankings
            return profile

    async def collect_by_id(self, provider_id: str) -> SalonProfile:
        """Collect one exact Yandex organization without a name search.

        City enrichment must never select a different similarly named business.
        """
        if not provider_id.isdigit():
            raise ValueError("Yandex provider ID must be numeric")
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
        html = await self._browser_html(f"{self.card_base_url}/{provider_id}/")
        organizations = self.parse_organizations(html)
        item = next(
            (
                organization
                for organization in organizations
                if str(organization.get("id")) == provider_id
            ),
            None,
        )
        if item is None:
            raise PlaceNotFoundError(
                f"Точная карточка Яндекс {provider_id} не найдена"
            )
        profile = self.normalize_organization(item)
        timeout = httpx.Timeout(30, connect=15)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            reviews, services, news, rankings, branches = await asyncio.gather(
                self._fetch_all_reviews(client, item),
                self._fetch_prices(client, item),
                self._fetch_news(item),
                self._fetch_search_rankings(item, profile.city),
                self._fetch_branches(item, profile.city),
            )
            profile.reviews = reviews
            self._set_review_coverage(profile, item)
            profile.services = services
            profile.news = news
            profile.stories = self._merge_stories(
                profile.stories, self.parse_stories_html(html)
            )
            profile.branches = branches or profile.branches
            profile.search_rankings = rankings
            return profile

    async def _browser_html(
        self, url: str, params: dict[str, str] | None = None
    ) -> str:
        async with AsyncSession(
            impersonate="chrome",
            timeout=30,
            verify=curl_ca_bundle(),
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
        ) as session:
            response = await session.get(url, params=params)
            response.raise_for_status()
            return response.text

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

    def _set_review_coverage(
        self,
        profile: SalonProfile,
        item: dict[str, Any],
    ) -> None:
        total = int((item.get("ratingData") or {}).get("reviewCount") or 0)
        profile.reviews_collected_count = len(profile.reviews)
        profile.reviews_total_count = total or None
        profile.reviews_truncated = total > self.max_review_pages * 50

    @classmethod
    def normalize_organization(cls, item: dict[str, Any]) -> SalonProfile:
        provider_id = str(item["id"])
        source_url = f"{cls.card_base_url}/{provider_id}/"
        rating_data = item.get("ratingData") or {}
        rating = rating_data.get("ratingValue")
        review_count = rating_data.get("reviewCount")
        booking_url = cls._booking_url(item.get("businessLinks", []))
        description = cls._description(item)
        coordinates = item.get("coordinates")
        longitude = latitude = None
        if (
            isinstance(coordinates, list)
            and len(coordinates) >= 2
            and all(isinstance(value, (int, float)) for value in coordinates[:2])
        ):
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
        region = item.get("region") if isinstance(item.get("region"), dict) else {}
        region_names = region.get("names") if isinstance(region.get("names"), dict) else {}
        address_components = item.get("addressComponents") or []
        district = next(
            (
                str(component.get("name"))
                for component in address_components
                if isinstance(component, dict)
                and str(component.get("kind") or "").casefold() in {"district", "area"}
                and component.get("name")
            ),
            None,
        )
        phones = [
            str(phone.get("value") or phone.get("number") or "").strip()
            for phone in item.get("phones") or []
            if isinstance(phone, dict) and (phone.get("value") or phone.get("number"))
        ]
        return SalonProfile(
            provider="yandex_maps",
            provider_id=provider_id,
            primary_provider="yandex_maps",
            name=item.get("title") or item.get("name"),
            address=item.get("fullAddress") or item.get("address"),
            city=region_names.get("nominative"),
            district=district,
            metro_stations=cls._metro_stations(item),
            latitude=latitude,
            longitude=longitude,
            phones=phones,
            description=description,
            categories=cls._categories(item),
            awards=cls._awards(item),
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
            masters=cls._masters(item),
            features=cls._features(item),
            stories=cls._stories(item),
            branches=cls._branches(item),
            media=cls._media(item),
            sources=[
                SourceRef(
                    provider="yandex_maps",
                    provider_id=provider_id,
                    url=source_url,
                )
            ],
            source_payloads={"yandex_maps": item},
        )

    @staticmethod
    def _features(item: dict[str, Any]) -> list[FeatureItem]:
        result: list[FeatureItem] = []
        features_by_id = {
            str(feature.get("id")): feature
            for feature in item.get("features") or []
            if isinstance(feature, dict) and feature.get("id")
        }
        groups = item.get("featureGroups") or item.get("feature_groups") or []
        if isinstance(groups, dict):
            groups = [groups]
        for group in groups:
            if not isinstance(group, dict):
                continue
            category = str(group.get("name") or group.get("title") or "").strip() or None
            raw_features = group.get("features") or group.get("items") or [
                features_by_id[feature_id]
                for feature_id in group.get("featureIds") or []
                if str(feature_id) in features_by_id
            ]
            for feature in raw_features:
                if not isinstance(feature, dict):
                    continue
                name = str(feature.get("name") or feature.get("title") or feature.get("id") or "").strip()
                value = feature.get("valueName", feature.get("value"))
                if isinstance(value, list):
                    value = ", ".join(
                        str(option.get("name") or option.get("value") or option)
                        for option in value
                    )
                if name:
                    result.append(FeatureItem(name=name, value=str(value) if value is not None else None, category=category, provider="yandex_maps"))
        return list({(x.category, x.name, x.value): x for x in result}.values())

    @staticmethod
    def _media(item: dict[str, Any]) -> list[MediaItem]:
        photos = item.get("photos") if isinstance(item.get("photos"), dict) else {}
        result: list[MediaItem] = []
        for position, raw in enumerate(photos.get("items") or []):
            if not isinstance(raw, dict):
                continue
            template = str(raw.get("urlTemplate") or raw.get("url") or "")
            if not template.startswith("http"):
                continue
            url = template.replace("%s", "orig")
            result.append(
                MediaItem(
                    provider_media_id=url.rsplit("/", 2)[-2],
                    media_type="photo",
                    url=url,
                    alt=raw.get("alt"),
                    category="Фотографии карточки",
                    position=position,
                )
            )
        return result

    @classmethod
    def _stories(cls, item: dict[str, Any]) -> list[StoryItem]:
        result: list[StoryItem] = []
        position = 0

        def visit(value: Any, category: str | None = None, inside: bool = False) -> None:
            nonlocal position
            if isinstance(value, list):
                for child in value:
                    visit(child, category, inside)
                return
            if not isinstance(value, dict):
                return
            tags = value.get("tags") if isinstance(value.get("tags"), list) else []
            local_category = str(value.get("categoryName") or value.get("category") or (tags[0] if tags else category) or "").strip() or None
            identifier = value.get("storyId") or (value.get("id") if inside else None)
            media = cls._story_media(value)
            title = value.get("title") or value.get("name")
            text = value.get("text") or value.get("description")
            is_story = bool(
                value.get("storyId")
                or value.get("screens")
                or ((title or text) and str(value.get("type") or "") not in {"photo", "video"})
            )
            if identifier is not None and is_story and (media or title or text):
                result.append(StoryItem(provider_story_id=str(identifier), title=title, text=text, category=local_category, media_urls=media, url=value.get("shareUrl") or value.get("targetUrl"), position=position))
                position += 1
            for key, child in value.items():
                visit(child, local_category, inside or "stor" in key.casefold())

        visit(item)
        return result

    @classmethod
    def parse_stories_html(cls, html: str) -> list[StoryItem]:
        result: list[StoryItem] = []
        for payload in cls._state_payloads(html):
            result.extend(cls._stories(payload))
        return cls._merge_stories([], result)

    @classmethod
    def _story_media(cls, value: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for nested in cls._walk(value):
            for key, raw in nested.items():
                if any(marker in key.casefold() for marker in ("image", "video", "media", "photo", "urltemplate")) and isinstance(raw, str) and raw.startswith("http"):
                    urls.append(raw.replace("%s", "XL"))
        return list(dict.fromkeys(urls))

    @staticmethod
    def _merge_stories(base: list[StoryItem], extra: list[StoryItem]) -> list[StoryItem]:
        return list({story.provider_story_id: story for story in [*base, *extra]}.values())

    @staticmethod
    def _branches(item: dict[str, Any]) -> list[BranchRef]:
        raw_items = item.get("branches") or item.get("relatedPlaces") or []
        if isinstance(raw_items, dict):
            raw_items = raw_items.get("items") or raw_items.get("places") or []
        result: list[BranchRef] = []
        for position, raw in enumerate(raw_items if isinstance(raw_items, list) else []):
            if not isinstance(raw, dict):
                continue
            branch_id = str(raw.get("id") or raw.get("oid") or "")
            if not branch_id:
                continue
            coordinates = raw.get("coordinates") or []
            result.append(BranchRef(provider_id=branch_id, name=str(raw.get("title") or raw.get("name") or "Филиал"), address=raw.get("fullAddress") or raw.get("address"), longitude=float(coordinates[0]) if isinstance(coordinates, list) and len(coordinates) > 1 else None, latitude=float(coordinates[1]) if isinstance(coordinates, list) and len(coordinates) > 1 else None, url=f"https://yandex.ru/maps/org/{branch_id}/", position=position))
        return result

    async def _fetch_branches(
        self,
        item: dict[str, Any],
        city: str | None,
    ) -> list[BranchRef]:
        name = str(item.get("title") or item.get("name") or "").strip()
        if not name:
            return []
        try:
            html = await self._browser_html(
                self.search_url,
                params={"text": " ".join(part for part in (name, city) if part)},
            )
        except (RequestsError, ValueError):
            return []
        expected = self._normalized_name(name)
        branches: list[BranchRef] = []
        for candidate in self.parse_organizations(html):
            candidate_name = str(candidate.get("title") or candidate.get("name") or "")
            if self._normalized_name(candidate_name) != expected:
                continue
            branch_id = str(candidate.get("id") or "")
            coordinates = candidate.get("coordinates") or []
            branches.append(
                BranchRef(
                    provider_id=branch_id,
                    name=candidate_name,
                    address=candidate.get("fullAddress") or candidate.get("address"),
                    longitude=float(coordinates[0]) if isinstance(coordinates, list) and len(coordinates) > 1 else None,
                    latitude=float(coordinates[1]) if isinstance(coordinates, list) and len(coordinates) > 1 else None,
                    url=f"https://yandex.ru/maps/org/{branch_id}/",
                    position=0 if branch_id == str(item.get("id")) else len(branches) + 1,
                )
            )
        return list({branch.provider_id: branch for branch in branches}.values())

    @staticmethod
    def _normalized_name(value: str) -> str:
        return re.sub(r"[^a-zа-я0-9]+", "", value.casefold().replace("ё", "е"))

    @staticmethod
    def _metro_stations(item: dict[str, Any]) -> list[str]:
        stations = []
        for station in item.get("metro") or []:
            if not isinstance(station, dict) or not station.get("name"):
                continue
            stations.append(str(station["name"]).strip())
        return list(dict.fromkeys(station for station in stations if station))

    async def _fetch_news(self, item: dict[str, Any]) -> list[NewsItem]:
        previews = item.get("eventsPreviews")
        if not isinstance(previews, dict):
            return []
        count = int(previews.get("count") or 0)
        list_uri = str(previews.get("uri") or "")
        if count <= 0 or not list_uri:
            return []

        provider_id = str(item["id"])
        slug = self._slug(item.get("seoname") or item.get("title") or "organization")
        referer = f"https://yandex.ru/maps/org/{slug}/{provider_id}/posts/"
        api_url = "https://yandex.ru/maps/api/posts/getPosts"
        news: list[NewsItem] = []
        csrf_token: str | None = None
        offset = 0
        try:
            async with AsyncSession(
                impersonate="chrome",
                headers={"Referer": referer, "X-Retpath-Y": referer},
                timeout=30,
                verify=curl_ca_bundle(),
            ) as session:
                while offset < count:
                    payload, csrf_token = await self._signed_api_get(
                        session,
                        api_url,
                        {
                            "ajax": "1",
                            "oid": provider_id,
                            "offset": str(offset),
                            "uri": list_uri,
                        },
                        csrf_token,
                    )
                    data = payload.get("data")
                    if not isinstance(data, dict):
                        break
                    raw_items = data.get("items")
                    if not isinstance(raw_items, list) or not raw_items:
                        break
                    news.extend(
                        self.parse_news_payload(payload, provider_id, slug)
                    )
                    offset += len(raw_items)
                    count = max(count, int(data.get("count") or count))
        except (RequestsError, ValueError, TypeError):
            return []

        result: list[NewsItem] = []
        seen: set[str] = set()
        for item_news in news:
            if item_news.provider_news_id not in seen:
                result.append(item_news)
                seen.add(item_news.provider_news_id)
        return result

    @classmethod
    def parse_news_payload(
        cls,
        payload: dict[str, Any],
        provider_id: str,
        slug: str = "organization",
    ) -> list[NewsItem]:
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return []
        result: list[NewsItem] = []
        for raw in data["items"]:
            if not isinstance(raw, dict):
                continue
            text = " ".join(
                str(raw.get("text") or raw.get("content") or "").split()
            )
            news_id = str(raw.get("id") or "")
            uri = str(raw.get("uri") or "")
            if not text or not news_id:
                continue
            photos: list[str] = []
            for photo in raw.get("photos") or []:
                if not isinstance(photo, dict):
                    continue
                template = photo.get("urlTemplate") or photo.get("url")
                if isinstance(template, str) and template.startswith("http"):
                    photos.append(template.replace("%s", "XL"))
            published_at = cls._timestamp_value(raw.get("publicationTime"))
            query = urllib.parse.urlencode(
                {"posts[uri]": uri},
                quote_via=urllib.parse.quote,
                safe="",
            )
            result.append(
                NewsItem(
                    provider_news_id=news_id,
                    text=text,
                    published_at=published_at,
                    photos=photos,
                    url=(
                        f"https://yandex.ru/maps/org/{slug}/{provider_id}/posts/"
                        f"?{query}"
                    ),
                )
            )
        return result

    async def _fetch_search_rankings(
        self,
        item: dict[str, Any],
        city: str | None,
    ) -> list[SearchRanking]:
        queries = self.ranking_queries or self._categories(item)[:3]
        if not queries:
            return []
        scope, scope_type, center, span = self._ranking_scope(item, city)
        rankings = await asyncio.gather(
            *(
                self._fetch_search_ranking(
                    query,
                    str(item["id"]),
                    item,
                    scope,
                    scope_type,
                    center,
                    span,
                )
                for query in queries
            ),
            return_exceptions=True,
        )
        return [ranking for ranking in rankings if isinstance(ranking, SearchRanking)]

    async def _fetch_search_ranking(
        self,
        query: str,
        provider_id: str,
        item: dict[str, Any],
        scope: str,
        scope_type: str,
        center: list[float],
        span: list[float],
    ) -> SearchRanking:
        region = item.get("region") if isinstance(item.get("region"), dict) else {}
        params = {
            "ajax": "1",
            "text": query,
            "lang": "ru_RU" if self.language.startswith("ru") else self.language,
            "yandex_gid": str(region.get("id") or item.get("geoId") or "0"),
            "origin": "maps-url",
            "results": "25",
            "ll": self._coordinate_text(center),
            "spn": self._coordinate_text(span),
            "rspn": "1",
            "snippets": (
                "businessrating/1.x,businessimages/1.x,subtitle/1.x,"
                "business_awards_experimental/1.x"
            ),
        }
        search_params = {
            "text": query,
            "ll": params["ll"],
            "spn": params["spn"],
            "rspn": "1",
        }
        search_url = "https://yandex.ru/maps/?" + urllib.parse.urlencode(
            search_params,
            quote_via=urllib.parse.quote,
            safe=",",
        )
        api_url = "https://yandex.ru/maps/api/search"
        referer = search_url
        csrf_token: str | None = None
        organic_results: list[dict[str, Any]] = []
        seen: set[str] = set()
        total_results: int | None = None
        current_data: dict[str, Any] | None = None

        try:
            async with AsyncSession(
                impersonate="chrome",
                headers={"Referer": referer, "X-Retpath-Y": referer},
                timeout=30,
                verify=curl_ca_bundle(),
            ) as session:
                for page in range(self.ranking_max_pages):
                    page_params = dict(params)
                    if page:
                        if current_data is None:
                            break
                        page_params.update(
                            {
                                "origin": "maps-scroll",
                                "skip": str(page * 25),
                                "ctx": str(current_data.get("requestContext") or ""),
                                "serpid": str(current_data.get("requestSerpId") or ""),
                                "parent_reqid": str(current_data.get("requestId") or ""),
                            }
                        )
                    payload, csrf_token = await self._signed_api_get(
                        session,
                        api_url,
                        page_params,
                        csrf_token,
                    )
                    data = payload.get("data")
                    if not isinstance(data, dict):
                        break
                    current_data = data
                    total_results = int(data.get("totalResultCount") or 0) or total_results
                    items = data.get("items")
                    if not isinstance(items, list) or not items:
                        break
                    for result in items:
                        if not isinstance(result, dict) or result.get("type") != "business":
                            continue
                        result_id = str(result.get("id") or "")
                        if not result_id or result_id in seen or result.get("isAdvert"):
                            continue
                        seen.add(result_id)
                        organic_results.append(result)
                    if provider_id in seen:
                        break
                    if total_results is not None and (page + 1) * 25 >= total_results:
                        break
        except (RequestsError, ValueError, TypeError):
            pass

        position = next(
            (
                index
                for index, result in enumerate(organic_results, start=1)
                if str(result.get("id")) == provider_id
            ),
            None,
        )
        return SearchRanking(
            query=query,
            position=position,
            total_results=total_results,
            checked_results=len(organic_results),
            scope=scope,
            scope_type=scope_type,
            search_url=search_url,
        )

    @classmethod
    async def _signed_api_get(
        cls,
        session: AsyncSession,
        url: str,
        params: dict[str, str],
        csrf_token: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        if csrf_token is None:
            bootstrap = await session.get(url, params=params)
            bootstrap.raise_for_status()
            bootstrap_payload = bootstrap.json()
            csrf_token = bootstrap_payload.get("csrfToken")
            if not csrf_token:
                return bootstrap_payload, None
        signed_params = {**params, "csrfToken": str(csrf_token)}
        signed_params["s"] = cls._query_signature(signed_params)
        response = await session.get(url, params=signed_params)
        response.raise_for_status()
        return response.json(), csrf_token

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
                verify=curl_ca_bundle(),
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
            sorted(params.items(), key=lambda pair: pair[0].casefold()),
            quote_via=urllib.parse.quote,
            safe="",
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
                verify=curl_ca_bundle(),
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
        seen: set[str | tuple[str, str]] = set()
        categories: list[dict[str, Any]] = []
        for payload in cls._state_payloads(html):
            for item in cls._walk(payload):
                category_items = item.get("categoryItems")
                if not isinstance(category_items, list):
                    continue
                categories.append(item)

        # Яндекс дублирует несколько услуг в категории popular. Сначала
        # обрабатываем реальные категории, чтобы дубль не потерял разбивку.
        categories.sort(
            key=lambda item: str(item.get("categoryName") or "").casefold()
            == "popular"
        )
        for category in categories:
            category_name = " ".join(
                str(category.get("categoryName") or "").split()
            )
            if category_name.casefold() == "popular":
                category_name = "Популярное"
            for raw in category["categoryItems"]:
                if not isinstance(raw, dict):
                    continue
                name = " ".join(
                    str(raw.get("title") or raw.get("name") or "").split()
                )
                price = cls._price_text(raw)
                if not name:
                    continue
                source_id = str(raw.get("sourceId") or "") or None
                key: str | tuple[str, str] = source_id or (
                    name.casefold(),
                    price or "",
                )
                if key in seen:
                    continue
                result.append(
                    Service(
                        name=name,
                        category=category_name or None,
                        provider_service_id=source_id,
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
    def _categories(item: dict[str, Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for category in item.get("categories") or []:
            if not isinstance(category, dict):
                continue
            name = " ".join(str(category.get("name") or "").split())
            key = name.casefold()
            if name and key not in seen:
                result.append(name)
                seen.add(key)
        return result

    @staticmethod
    def _awards(item: dict[str, Any]) -> list[str]:
        result: list[str] = []
        awards = item.get("awards")
        if isinstance(awards, dict):
            good_place = awards.get("goodPlaceYear")
            if good_place:
                result.append(f"Хорошее место {good_place}")
            if awards.get("ultimaGuide"):
                result.append("Ultima Guide")

        # In some cards Yandex exposes only the compact award marker.
        snippet = item.get("modularSnippet")
        snippet_awards = snippet.get("awards") if isinstance(snippet, dict) else []
        if 0 in (snippet_awards or []) and not any(
            award.startswith("Хорошее место") for award in result
        ):
            result.append("Хорошее место")
        return result

    @staticmethod
    def _masters(item: dict[str, Any]) -> list[str]:
        """Read only explicitly published, active staff from the Maps card.

        Booking providers are deliberately ignored here. Their historic staff
        entries belong to a separate online-booking report and must not leak
        into the Yandex Maps view.
        """
        result: list[str] = []
        seen: set[str] = set()
        raw_staff: list[Any] = []
        for key in ("masters", "staff", "specialists"):
            value = item.get(key)
            if isinstance(value, list):
                raw_staff.extend(value)
            elif isinstance(value, dict):
                nested = value.get("items") or value.get("results")
                if isinstance(nested, list):
                    raw_staff.extend(nested)

        inactive_statuses = {"inactive", "disabled", "archived", "deleted"}
        for raw in raw_staff:
            if isinstance(raw, str):
                name = " ".join(raw.split())
                role = ""
            elif isinstance(raw, dict):
                if any(raw.get(flag) is False for flag in ("active", "isActive", "enabled")):
                    continue
                if str(raw.get("status") or "").casefold() in inactive_statuses:
                    continue
                name = " ".join(
                    str(raw.get("name") or raw.get("title") or raw.get("displayName") or "").split()
                )
                role = " ".join(
                    str(raw.get("specialization") or raw.get("role") or raw.get("position") or "").split()
                )
            else:
                continue
            rendered = name + (f" — {role}" if role else "")
            key = rendered.casefold()
            if name and key not in seen:
                result.append(rendered)
                seen.add(key)
        return result

    @classmethod
    def _ranking_scope(
        cls,
        item: dict[str, Any],
        requested_city: str | None,
    ) -> tuple[str, str, list[float], list[float]]:
        region = item.get("region") if isinstance(item.get("region"), dict) else {}
        composite = (
            item.get("compositeAddress")
            if isinstance(item.get("compositeAddress"), dict)
            else {}
        )
        city = " ".join(
            str(
                composite.get("locality")
                or (region.get("names") or {}).get("nominative")
                or requested_city
                or "город"
            ).split()
        )
        coordinates = item.get("coordinates")
        if not (
            isinstance(coordinates, list)
            and len(coordinates) >= 2
            and all(isinstance(value, (int, float)) for value in coordinates[:2])
        ):
            coordinates = [region.get("longitude") or 0, region.get("latitude") or 0]
        center = [float(coordinates[0]), float(coordinates[1])]

        normalized_city = cls._normalized(city)
        federal_city = normalized_city in {
            "москва",
            "мск",
            "санктпетербург",
            "спб",
            "питер",
        }
        metro = item.get("metro")
        if federal_city and isinstance(metro, list) and metro:
            candidates = [station for station in metro if isinstance(station, dict)]
            candidates.sort(key=lambda station: station.get("distanceValue") or math.inf)
            station = candidates[0] if candidates else {}
            station_name = " ".join(str(station.get("name") or "ближайшая станция").split())
            station_coordinates = station.get("coordinates")
            if isinstance(station_coordinates, list) and len(station_coordinates) >= 2:
                center = [float(station_coordinates[0]), float(station_coordinates[1])]
            return f"метро {station_name}", "metro", center, [0.04, 0.03]

        zoom = region.get("zoom")
        is_large = federal_city or (isinstance(zoom, (int, float)) and zoom <= 10)
        if is_large:
            district = " ".join(
                str(composite.get("district") or composite.get("area") or "").split()
            )
            if not district:
                district = "район рядом с заведением"
            return district, "district", center, [0.10, 0.07]

        bounds = region.get("bounds")
        if (
            isinstance(bounds, list)
            and len(bounds) >= 2
            and all(isinstance(point, list) and len(point) >= 2 for point in bounds[:2])
        ):
            west, south = bounds[0][:2]
            east, north = bounds[1][:2]
            center = [(float(west) + float(east)) / 2, (float(south) + float(north)) / 2]
            span = [max(float(east) - float(west), 0.02), max(float(north) - float(south), 0.02)]
        else:
            span = [0.34, 0.23]
        return city, "city", center, span

    @staticmethod
    def _coordinate_text(values: list[float]) -> str:
        return ",".join(f"{float(value):.6f}".rstrip("0").rstrip(".") for value in values)

    @staticmethod
    def _timestamp_value(value: Any) -> str | None:
        if value in (None, ""):
            return None
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return str(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)

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
