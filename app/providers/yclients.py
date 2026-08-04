import asyncio
import base64
import json
import os
import re
import time
import urllib.parse
import uuid
from datetime import date, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from curl_cffi.requests import AsyncSession, RequestsError

from app.models import BranchRef, Review, SalonProfile, Service, SourceRef
from app.providers.base import ProfileEnricher


class YClientsEnricher(ProfileEnricher):
    base_url = "https://api.yclients.com/api/v1"
    platform_base_url = (
        "https://platform.yclients.com/api/v1/b2c/booking/availability"
    )
    _public_configs: dict[str, tuple[str, str]] = {}

    def __init__(
        self,
        partner_token: str | None = None,
        user_token: str | None = None,
    ) -> None:
        self.partner_token = partner_token
        self.user_token = user_token

    async def enrich(self, profile: SalonProfile) -> SalonProfile:
        form_id = self._form_id(str(profile.booking_url or ""))
        if form_id is None:
            return profile
        try:
            profile = await self._enrich_public(profile, form_id)
        except Exception:
            pass
        if self.partner_token:
            try:
                profile = await self._enrich_official(profile)
            except Exception:
                pass
        return profile

    async def _enrich_official(self, profile: SalonProfile) -> SalonProfile:
        form_id = self._form_id(str(profile.booking_url or ""))
        if form_id is None:
            return profile
        authorization = f"Bearer {self.partner_token}"
        if self.user_token:
            authorization += f", User {self.user_token}"
        headers = {
            "Authorization": authorization,
            "Accept": "application/vnd.yclients.v2+json",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=25,
        ) as client:
            form_response = await client.get(f"/bookform/{form_id}")
            form_response.raise_for_status()
            form_data = self._data(form_response.json())
            company_id = self._company_id(form_data)
            if company_id is None:
                return profile

            services_response, staff_response = await asyncio.gather(
                client.get(f"/book_services/{company_id}"),
                client.get(f"/book_staff/{company_id}"),
            )
            services_response.raise_for_status()
            staff_response.raise_for_status()
            services = self.parse_services(
                self._data(services_response.json()),
                str(profile.booking_url),
            )
            staff = self.parse_staff(self._data(staff_response.json()))
            slots = await self._fetch_slots(
                client,
                company_id,
                services,
                self._raw_staff(self._data(staff_response.json())),
            )
            comments = (
                await self._fetch_comments(client, company_id)
                if self.user_token
                else []
            )

        branches = self.parse_branches(form_data, str(profile.booking_url))
        if branches:
            profile.branches = self._merge_branches(profile.branches, branches)

        if services:
            profile.services = self._merge_services(profile.services, services)
        if staff:
            profile.masters = list(dict.fromkeys([*profile.masters, *staff]))
        if slots:
            profile.available_slots = list(
                dict.fromkeys([*profile.available_slots, *slots])
            )
        if comments:
            profile.reviews = self._merge_reviews(profile.reviews, comments)
        if not any(
            source.provider == "yclients" and source.provider_id == str(company_id)
            for source in profile.sources
        ):
            profile.sources.append(
                SourceRef(
                    provider="yclients",
                    provider_id=str(company_id),
                    url=profile.booking_url,
                )
            )
        return profile

    async def _enrich_public(
        self,
        profile: SalonProfile,
        form_id: int,
    ) -> SalonProfile:
        booking_url = str(profile.booking_url)
        parsed_url = urllib.parse.urlsplit(booking_url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        token, app_version = await self._public_config(origin)

        async with AsyncSession(
            impersonate="chrome",
            timeout=30,
            max_clients=8,
        ) as client:
            form_response = await client.get(
                f"{origin}/api/v1/bookform/{form_id}/",
                headers=self._public_headers(token, app_version, origin),
            )
            form_response.raise_for_status()
            form_data = self._data(form_response.json())
            company_id = self._company_id(form_data)
            if company_id is None:
                return profile

            services_response, staff_response = await asyncio.gather(
                client.get(
                    f"{self.platform_base_url}/book_services/{company_id}",
                    params={"without_seances": "1"},
                    headers=self._public_headers(
                        token,
                        app_version,
                        origin,
                    ),
                ),
                client.get(
                    f"{self.platform_base_url}/book_staff/{company_id}",
                    params={"without_seances": "1"},
                    headers=self._public_headers(
                        token,
                        app_version,
                        origin,
                    ),
                ),
            )
            services_response.raise_for_status()
            staff_response.raise_for_status()
            services_data = services_response.json()
            staff_data = staff_response.json()
            services = self.parse_services(services_data, booking_url)
            staff = self.parse_staff(staff_data)
            slots = await self._fetch_public_slots(
                client,
                company_id,
                services,
                self._raw_staff(staff_data),
                token,
                app_version,
                origin,
            )

        branches = self.parse_branches(form_data, booking_url)
        if branches:
            profile.branches = self._merge_branches(profile.branches, branches)

        if services:
            profile.services = self._merge_services(profile.services, services)
        if staff:
            profile.masters = list(dict.fromkeys([*profile.masters, *staff]))
        if slots:
            profile.available_slots = list(
                dict.fromkeys([*profile.available_slots, *slots])
            )
        if not any(
            source.provider == "yclients" and source.provider_id == str(company_id)
            for source in profile.sources
        ):
            profile.sources.append(
                SourceRef(
                    provider="yclients",
                    provider_id=str(company_id),
                    url=profile.booking_url,
                )
            )
        return profile

    @classmethod
    async def _public_config(cls, origin: str) -> tuple[str, str]:
        cached = cls._public_configs.get(origin)
        if cached:
            return cached
        async with AsyncSession(
            impersonate="chrome",
            timeout=30,
            max_clients=8,
        ) as client:
            root_response = await client.get(origin + "/")
            root_response.raise_for_status()
            main_match = re.search(
                r'<script[^>]+src=["\']([^"\']*main-[^"\']+\.js)',
                root_response.text,
            )
            if main_match is None:
                raise ValueError("Не найден основной модуль YCLIENTS")
            main_url = urllib.parse.urljoin(origin + "/", main_match.group(1))
            main_response = await client.get(main_url)
            main_response.raise_for_status()
            chunk_urls = [
                urllib.parse.urljoin(main_url, chunk)
                for chunk in dict.fromkeys(
                    re.findall(r"[\w-]+\.js", main_response.text)
                )
            ]

            async def fetch_bundle(url: str) -> str:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.text
                except RequestsError:
                    return ""

            bundles = await asyncio.gather(
                *(fetch_bundle(url) for url in chunk_urls)
            )
        for bundle in bundles:
            token_match = re.search(r'apiToken:"Bearer ([^"]+)"', bundle)
            version_match = re.search(
                r'name:"client\.booking".{0,160}?version:"([^"]+)"',
                bundle,
            )
            if token_match and version_match:
                config = (token_match.group(1), version_match.group(1))
                cls._public_configs[origin] = config
                return config
        raise ValueError("Не найдена публичная конфигурация YCLIENTS")

    @staticmethod
    def _public_headers(
        token: str,
        app_version: str,
        origin: str,
    ) -> dict[str, str]:
        analytics_udid = str(uuid.uuid4())
        nonce = os.urandom(12)
        payload = json.dumps(
            {
                "requestUdid": str(uuid.uuid4()),
                "timestamp": int(time.time()),
            },
            separators=(",", ":"),
        ).encode()
        encrypted = AESGCM(analytics_udid[:32].encode()).encrypt(
            nonce,
            payload,
            None,
        )
        context = (
            base64.b64encode(nonce).decode()
            + ":"
            + base64.b64encode(encrypted).decode()
        )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": origin + "/",
            "X-App-Client-Context": context,
            "X-App-Client-Context-Version": "2",
            "X-App-Client-Context-Analytics-Udid": analytics_udid,
            "X-App-Signature": "",
            "X-Yclients-Application-Action": "",
            "X-Yclients-Application-Name": "client.booking",
            "X-Yclients-Application-Platform": "angular-18.2.13",
            "X-Yclients-Application-Version": app_version,
        }

    async def _fetch_public_slots(
        self,
        client: AsyncSession,
        company_id: int,
        services: list[Service],
        staff: list[dict[str, Any]],
        token: str,
        app_version: str,
        origin: str,
    ) -> list[str]:
        staff_by_id = {
            str(item["id"]): item.get("name") or f"мастер {item['id']}"
            for item in staff
            if item.get("id")
        }
        today = date.today().isoformat()
        end_date = (date.today() + timedelta(days=14)).isoformat()
        slots: list[str] = []
        for service in services[:5]:
            service_id = service.provider_service_id
            if not service_id:
                continue
            search_staff_response = await client.post(
                f"{self.platform_base_url}/search-staff",
                json={
                    "context": {"location_id": company_id},
                    "filter": {
                        "datetime": None,
                        "records": [
                            {
                                "staff_id": None,
                                "attendance_service_items": [
                                    {
                                        "type": "service",
                                        "id": int(service_id),
                                    }
                                ],
                            }
                        ],
                    },
                },
                headers=self._public_headers(token, app_version, origin),
            )
            if search_staff_response.status_code != 200:
                continue
            matching_staff = [
                item
                for item in search_staff_response.json().get("data", [])
                if isinstance(item, dict)
                and (item.get("attributes") or {}).get("is_bookable")
            ]
            if not matching_staff:
                continue
            staff_id = str(matching_staff[0].get("id") or "")
            if not staff_id:
                continue
            dates_response = await client.get(
                f"{self.platform_base_url}/book_dates/{company_id}",
                params={
                    "date_from": today,
                    "date_to": end_date,
                    "service_ids[]": service_id,
                    "staff_id": staff_id,
                },
                headers=self._public_headers(token, app_version, origin),
            )
            if dates_response.status_code != 200:
                continue
            dates = self._date_strings(dates_response.json())
            for slot_date in dates[:4]:
                times_response = await client.get(
                    f"{self.platform_base_url}/book_times/"
                    f"{company_id}/{staff_id}/{slot_date}",
                    params={"service_ids[]": service_id},
                    headers=self._public_headers(
                        token,
                        app_version,
                        origin,
                    ),
                )
                if times_response.status_code != 200:
                    continue
                times = self._time_strings(times_response.json())
                if not times:
                    continue
                master_name = staff_by_id.get(
                    staff_id,
                    f"мастер {staff_id}",
                )
                slots.extend(
                    f"{slot_date} {value} — {master_name} · {service.name}"
                    for value in times[:3]
                )
                break
            if len(slots) >= 15:
                break
        return slots[:15]

    async def _fetch_comments(
        self,
        client: httpx.AsyncClient,
        company_id: int,
    ) -> list[Review]:
        result: list[Review] = []
        for page in range(1, 101):
            response = await client.get(
                f"/comments/{company_id}/",
                params={"page": page, "count": 100},
            )
            response.raise_for_status()
            data = self._data(response.json())
            comments = self.parse_comments(data)
            result.extend(comments)
            if len(comments) < 100:
                break
        return result

    @staticmethod
    def parse_comments(data: Any) -> list[Review]:
        result: list[Review] = []
        seen: set[str] = set()
        for item in YClientsEnricher._objects(data):
            text = " ".join(str(item.get("text") or "").split())
            review_id = item.get("id")
            if not text or review_id is None or "rating" not in item:
                continue
            key = str(review_id)
            if key in seen:
                continue
            result.append(
                Review(
                    provider="yclients",
                    provider_review_id=key,
                    author=str(item.get("user_name") or "Клиент YCLIENTS"),
                    rating=item.get("rating"),
                    text=text,
                    published_at=(
                        str(item["date"]) if item.get("date") is not None else None
                    ),
                )
            )
            seen.add(key)
        return result

    @staticmethod
    def parse_services(data: Any, source_url: str) -> list[Service]:
        result: list[Service] = []
        seen: set[tuple[str, str]] = set()
        for item in YClientsEnricher._objects(data):
            title = item.get("title")
            if not title or not (
                "price_min" in item
                or "price_max" in item
                or "seance_length" in item
            ):
                continue
            price = YClientsEnricher._price(
                item.get("price_min"),
                item.get("price_max"),
            )
            duration = YClientsEnricher._duration(item.get("seance_length"))
            key = (str(title).casefold(), price or "")
            if key in seen:
                continue
            result.append(
                Service(
                    name=str(title),
                    provider_service_id=(
                        str(item["id"]) if item.get("id") is not None else None
                    ),
                    price=price,
                    duration=duration,
                    provider="yclients",
                    source_url=source_url,
                )
            )
            seen.add(key)
        return result

    @staticmethod
    def parse_staff(data: Any) -> list[str]:
        result: list[str] = []
        for item in YClientsEnricher._objects(data):
            name = item.get("name")
            if not name or "bookable" not in item:
                continue
            details = item.get("specialization") or item.get("position")
            result.append(f"{name} — {details}" if details else str(name))
        return list(dict.fromkeys(result))

    async def _fetch_slots(
        self,
        client: httpx.AsyncClient,
        company_id: int,
        services: list[Service],
        staff: list[dict[str, Any]],
    ) -> list[str]:
        service_ids = [
            service.provider_service_id
            for service in services
            if service.provider_service_id
        ]
        params: list[tuple[str, str]] = [
            ("date_from", date.today().isoformat()),
            ("date_to", (date.today() + timedelta(days=14)).isoformat()),
        ]
        for service_id in service_ids[:3]:
            params.append(("service_ids[]", str(service_id)))
        slots: list[str] = []
        for master in staff[:5]:
            staff_id = master.get("id")
            if not staff_id:
                continue
            try:
                dates_response = await client.get(
                    f"/book_dates/{company_id}",
                    params=[*params, ("staff_id", str(staff_id))],
                )
                dates_response.raise_for_status()
                dates = self._date_strings(self._data(dates_response.json()))
                if not dates:
                    continue
                times_response = await client.get(
                    f"/book_times/{company_id}/{staff_id}/{dates[0]}",
                    params=[
                        pair
                        for pair in params
                        if pair[0] == "service_ids[]"
                    ],
                )
                times_response.raise_for_status()
                master_name = master.get("name") or f"мастер {staff_id}"
                for value in self._time_strings(self._data(times_response.json()))[:3]:
                    slots.append(f"{dates[0]} {value} — {master_name}")
            except httpx.HTTPError:
                continue
        return slots[:15]

    @staticmethod
    def _form_id(url: str) -> int | None:
        match = re.search(r"https?://n(\d+)\.yclients\.com", url, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def _company_id(data: Any) -> int | None:
        for item in YClientsEnricher._objects(data):
            for key in ("company_id", "companyId"):
                value = item.get(key)
                if value is not None and str(value).isdigit():
                    return int(value)
            company = item.get("company")
            if isinstance(company, dict) and str(company.get("id", "")).isdigit():
                return int(company["id"])
        return None

    @classmethod
    def parse_branches(cls, data: Any, booking_url: str) -> list[BranchRef]:
        result: list[BranchRef] = []

        def walk(value: Any, inside: bool = False) -> None:
            if isinstance(value, list):
                for child in value:
                    walk(child, inside)
                return
            if not isinstance(value, dict):
                return
            identifier = value.get("company_id") or value.get("companyId") or value.get("id")
            name = value.get("title") or value.get("name")
            address = value.get("address") or value.get("address_name")
            if inside and identifier and name and address:
                result.append(
                    BranchRef(
                        provider_id=str(identifier),
                        name=str(name),
                        address=str(address),
                        latitude=value.get("latitude") or value.get("lat"),
                        longitude=value.get("longitude") or value.get("lon"),
                        url=booking_url,
                        position=len(result),
                    )
                )
            for key, child in value.items():
                walk(
                    child,
                    inside
                    or any(marker in key.casefold() for marker in ("branch", "compan", "location", "salon")),
                )

        walk(data)
        return list({branch.provider_id: branch for branch in result}.values())

    @staticmethod
    def _merge_branches(
        base: list[BranchRef], extra: list[BranchRef]
    ) -> list[BranchRef]:
        return list({branch.provider_id: branch for branch in [*base, *extra]}.values())

    @staticmethod
    def _data(payload: Any) -> Any:
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    @staticmethod
    def _objects(value: Any):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from YClientsEnricher._objects(item)
        elif isinstance(value, list):
            for item in value:
                yield from YClientsEnricher._objects(item)

    @staticmethod
    def _raw_staff(data: Any) -> list[dict[str, Any]]:
        return [
            item
            for item in YClientsEnricher._objects(data)
            if item.get("name") and "bookable" in item and item.get("id")
        ]

    @staticmethod
    def _price(minimum: Any, maximum: Any) -> str | None:
        if minimum in (None, "") and maximum in (None, ""):
            return None
        if maximum in (None, "") or minimum == maximum:
            return f"{minimum} ₽"
        if minimum in (None, ""):
            return f"до {maximum} ₽"
        return f"{minimum}–{maximum} ₽"

    @staticmethod
    def _duration(seconds: Any) -> str | None:
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            return None
        minutes = int(seconds // 60)
        return f"{minutes} мин"

    @staticmethod
    def _date_strings(data: Any) -> list[str]:
        result: list[str] = []
        def collect(value: Any) -> None:
            if isinstance(value, str):
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    result.append(value)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        collect(data)
        return list(dict.fromkeys(result))

    @staticmethod
    def _time_strings(data: Any) -> list[str]:
        result: list[str] = []
        for item in YClientsEnricher._objects(data):
            value = item.get("time")
            if isinstance(value, str):
                result.append(value)
            elif isinstance(item.get("datetime"), str):
                result.append(item["datetime"].split("T")[-1][:5])
        return list(dict.fromkeys(result))

    @staticmethod
    def _merge_services(base: list[Service], extra: list[Service]) -> list[Service]:
        result = list(base)
        seen = {
            (service.provider or "", service.name.casefold(), service.price or "")
            for service in base
        }
        for service in extra:
            key = (service.provider or "", service.name.casefold(), service.price or "")
            if key not in seen:
                result.append(service)
                seen.add(key)
        return result

    @staticmethod
    def _merge_reviews(base: list[Review], extra: list[Review]) -> list[Review]:
        result = list(base)
        seen = {
            (review.provider, review.provider_review_id)
            for review in base
            if review.provider_review_id
        }
        for review in extra:
            key = (review.provider, review.provider_review_id)
            if key not in seen:
                result.append(review)
                seen.add(key)
        return result
