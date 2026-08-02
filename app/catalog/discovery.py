from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any

from curl_cffi.requests import AsyncSession, RequestsError
import httpx

from app.catalog.domain import (
    CitySpec,
    DiscoveryCard,
    DiscoveryCursor,
    DiscoveryPage,
    DiscoveryScope,
)
from app.providers.yandex import YandexMapsProvider


class DiscoveryError(RuntimeError):
    def __init__(self, code: str, *, blocked: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.blocked = blocked


class YandexCityDiscovery:
    provider = "yandex_maps"
    api_url = "https://yandex.ru/maps/api/search"

    def __init__(self, language: str = "ru") -> None:
        self.language = language

    async def fetch_page(
        self,
        city: CitySpec,
        category_query: str,
        scope: DiscoveryScope,
        cursor: DiscoveryCursor,
    ) -> DiscoveryPage:
        center_lon, center_lat = scope.center
        span_lon, span_lat = scope.span
        search_params = {
            "text": category_query,
            "ll": f"{center_lon:.6f},{center_lat:.6f}",
            "spn": f"{span_lon:.6f},{span_lat:.6f}",
            "rspn": "1",
        }
        referer = "https://yandex.ru/maps/?" + urllib.parse.urlencode(
            search_params,
            quote_via=urllib.parse.quote,
            safe=",",
        )
        params = {
            "ajax": "1",
            "text": category_query,
            "lang": "ru_RU" if self.language.startswith("ru") else self.language,
            "yandex_gid": city.yandex_geo_id,
            "origin": "maps-url" if cursor.page == 0 else "maps-scroll",
            "results": "25",
            "ll": search_params["ll"],
            "spn": search_params["spn"],
            "rspn": "1",
            "snippets": (
                "businessrating/1.x,businessimages/1.x,subtitle/1.x,"
                "business_awards_experimental/1.x"
            ),
        }
        if cursor.page:
            params.update(
                {
                    "skip": str(cursor.skip),
                    "ctx": cursor.context or "",
                    "serpid": cursor.serp_id or "",
                    "parent_reqid": cursor.parent_request_id or "",
                }
            )
        try:
            async with AsyncSession(
                impersonate="chrome",
                headers={"Referer": referer, "X-Retpath-Y": referer},
                timeout=30,
            ) as session:
                payload, _csrf = await YandexMapsProvider._signed_api_get(
                    session,
                    self.api_url,
                    params,
                    None,
                )
        except RequestsError as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            blocked = status in {403, 429}
            raise DiscoveryError(
                f"yandex_http_{status or 'network'}",
                blocked=blocked,
            ) from error
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise DiscoveryError("yandex_contract_error") from error
        return self.parse_discovery_page(payload, cursor)

    @classmethod
    def parse_discovery_page(
        cls,
        payload: dict[str, Any],
        cursor: DiscoveryCursor | None = None,
    ) -> DiscoveryPage:
        cursor = cursor or DiscoveryCursor()
        data = payload.get("data")
        if not isinstance(data, dict):
            data = payload.get("search") if isinstance(payload.get("search"), dict) else {}
        if not data:
            raise DiscoveryError("yandex_contract_missing_results")
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raw_items = data.get("results") if isinstance(data.get("results"), list) else None
        if raw_items is None:
            raise DiscoveryError("yandex_contract_missing_results")
        cards: list[DiscoveryCard] = []
        page_ids: list[str] = []
        for item in raw_items:
            if not isinstance(item, dict) or item.get("type", "business") != "business":
                continue
            provider_id = str(item.get("id") or "")
            name = str(item.get("title") or item.get("name") or "").strip()
            if not provider_id.isdigit() or not name:
                continue
            coordinates = item.get("coordinates")
            longitude = latitude = None
            if (
                isinstance(coordinates, list)
                and len(coordinates) >= 2
                and all(isinstance(value, (int, float)) for value in coordinates[:2])
            ):
                longitude, latitude = float(coordinates[0]), float(coordinates[1])
            rating_data = item.get("ratingData") if isinstance(item.get("ratingData"), dict) else {}
            categories = [
                str(category.get("name")).strip()
                for category in item.get("categories") or []
                if isinstance(category, dict) and category.get("name")
            ]
            phones = [
                str(phone.get("value") or phone.get("number") or "").strip()
                for phone in item.get("phones") or []
                if isinstance(phone, dict) and (phone.get("value") or phone.get("number"))
            ]
            website_domain = cls._website_domain(item)
            booking_identity = cls._booking_identity(item)
            cards.append(
                DiscoveryCard(
                    provider="yandex_maps",
                    provider_id=provider_id,
                    name=name,
                    address=item.get("fullAddress") or item.get("address"),
                    longitude=longitude,
                    latitude=latitude,
                    phones=phones,
                    website_domain=website_domain,
                    booking_identity=booking_identity,
                    categories=list(dict.fromkeys(categories)),
                    rating=rating_data.get("ratingValue"),
                    reviews_count=rating_data.get("reviewCount"),
                    source_url=f"https://yandex.ru/maps/org/{provider_id}/",
                    is_advert=bool(item.get("isAdvert")),
                )
            )
            page_ids.append(provider_id)
        total_hint = _safe_int(data.get("totalResultCount"))
        next_cursor = None
        if cards and (total_hint is None or cursor.skip + len(raw_items) < total_hint):
            next_cursor = DiscoveryCursor(
                page=cursor.page + 1,
                skip=cursor.skip + len(raw_items),
                context=str(data.get("requestContext") or "") or None,
                serp_id=str(data.get("requestSerpId") or "") or None,
                parent_request_id=str(data.get("requestId") or "") or None,
            )
        raw_hash = hashlib.sha256(
            "|".join(page_ids).encode("utf-8")
        ).hexdigest()
        return DiscoveryPage(
            cards=cards,
            cursor=cursor,
            next_cursor=next_cursor,
            total_hint=total_hint,
            raw_hash=raw_hash,
        )

    @staticmethod
    def _website_domain(item: dict[str, Any]) -> str | None:
        website = item.get("site") or item.get("website")
        if not website:
            for link in item.get("businessLinks") or []:
                if isinstance(link, dict) and link.get("type") == "website":
                    website = link.get("href")
                    break
        if not website:
            return None
        parsed = urllib.parse.urlparse(str(website) if "://" in str(website) else f"https://{website}")
        return (parsed.hostname or "").casefold().removeprefix("www.") or None

    @staticmethod
    def _booking_identity(item: dict[str, Any]) -> str | None:
        for link in item.get("businessLinks") or []:
            if not isinstance(link, dict) or link.get("type") != "booking":
                continue
            parsed = urllib.parse.urlparse(str(link.get("href") or ""))
            if parsed.hostname:
                return f"{parsed.hostname.casefold()}{parsed.path.rstrip('/')}"
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class TwoGisCityDiscovery:
    provider = "2gis"
    api_url = "https://catalog.api.2gis.com/3.0/items"

    def __init__(self, api_key: str, language: str = "ru") -> None:
        self.api_key = api_key
        self.locale = "ru_RU" if language.startswith("ru") else "en_RU"

    async def fetch_page(
        self,
        city: CitySpec,
        category_query: str,
        scope: DiscoveryScope,
        cursor: DiscoveryCursor,
    ) -> DiscoveryPage:
        params = {
            "q": f"{category_query} {city.name}",
            "key": self.api_key,
            "locale": self.locale,
            "page": str(cursor.page + 1),
            "page_size": "50",
            "fields": ",".join(
                (
                    "items.address",
                    "items.point",
                    "items.reviews",
                    "items.contact_groups",
                    "items.rubrics",
                )
            ),
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(self.api_url, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise DiscoveryError(
                f"twogis_http_{status}", blocked=status in {403, 429}
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise DiscoveryError("twogis_network_or_contract") from error
        page = self.parse_discovery_page(payload, cursor)
        return page.model_copy(
            update={
                "cards": [card for card in page.cards if self._inside_scope(card, scope)]
            }
        )

    @staticmethod
    def _inside_scope(card: DiscoveryCard, scope: DiscoveryScope) -> bool:
        return (
            card.longitude is not None
            and card.latitude is not None
            and scope.west <= card.longitude <= scope.east
            and scope.south <= card.latitude <= scope.north
        )

    @classmethod
    def parse_discovery_page(
        cls,
        payload: dict[str, Any],
        cursor: DiscoveryCursor | None = None,
    ) -> DiscoveryPage:
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        meta_code = _safe_int(meta.get("code"))
        if meta_code is not None and meta_code != 200:
            raise DiscoveryError(
                f"twogis_contract_{meta_code}",
                blocked=meta_code in {403, 429},
            )
        cursor = cursor or DiscoveryCursor()
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        items = result.get("items") if isinstance(result.get("items"), list) else []
        cards: list[DiscoveryCard] = []
        ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("id") or "")
            name = str(item.get("name") or "").strip()
            if not provider_id or not name:
                continue
            point = item.get("point") if isinstance(item.get("point"), dict) else {}
            reviews = item.get("reviews") if isinstance(item.get("reviews"), dict) else {}
            categories = [
                str(rubric.get("name")).strip()
                for rubric in item.get("rubrics") or []
                if isinstance(rubric, dict) and rubric.get("name")
            ]
            phones: list[str] = []
            website_domain = None
            for group in item.get("contact_groups") or []:
                if not isinstance(group, dict):
                    continue
                for contact in group.get("contacts") or []:
                    if not isinstance(contact, dict):
                        continue
                    if contact.get("type") == "phone" and contact.get("value"):
                        phones.append(str(contact["value"]))
                    if contact.get("type") in {"website", "url"}:
                        raw_url = str(contact.get("url") or contact.get("value") or "")
                        parsed = urllib.parse.urlparse(
                            raw_url if "://" in raw_url else f"https://{raw_url}"
                        )
                        website_domain = (parsed.hostname or "").casefold().removeprefix("www.") or None
            cards.append(
                DiscoveryCard(
                    provider="2gis",
                    provider_id=provider_id,
                    name=name,
                    address=item.get("full_name") or item.get("address_name"),
                    longitude=_safe_float(point.get("lon")),
                    latitude=_safe_float(point.get("lat")),
                    phones=phones,
                    website_domain=website_domain,
                    categories=list(dict.fromkeys(categories)),
                    rating=reviews.get("general_rating") or reviews.get("org_rating"),
                    reviews_count=reviews.get("general_review_count_with_stars")
                    or reviews.get("general_review_count"),
                    source_url=f"https://2gis.ru/firm/{provider_id}",
                )
            )
            ids.append(provider_id)
        total = _safe_int(result.get("total"))
        next_cursor = None
        consumed = cursor.skip + len(items)
        if cards and (total is None or consumed < total):
            next_cursor = DiscoveryCursor(
                page=cursor.page + 1,
                skip=consumed,
            )
        return DiscoveryPage(
            cards=cards,
            cursor=cursor,
            next_cursor=next_cursor,
            total_hint=total,
            raw_hash=hashlib.sha256("|".join(ids).encode()).hexdigest(),
        )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
