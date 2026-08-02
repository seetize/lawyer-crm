from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.catalog.competitors import compare_locations, compute_competitors
from app.catalog.db import CatalogRepository
from app.catalog.discovery import DiscoveryError, TwoGisCityDiscovery, YandexCityDiscovery
from app.catalog.domain import (
    CitySpec,
    DiscoveryCard,
    DiscoveryCursor,
    DiscoveryPage,
    DiscoveryScope,
)
from app.catalog.service import CityCatalogService
from app.models import Review, SalonProfile, Service


def city_spec() -> CitySpec:
    return CitySpec(
        name="Ярославль",
        yandex_geo_id="16",
        scope=DiscoveryScope(
            west=39.7,
            south=57.5,
            east=40.05,
            north=57.75,
        ),
    )


def card(
    provider_id: str,
    name: str = "Студия Лак",
    address: str = "Ярославль, улица Свободы, 1",
    longitude: float = 39.89,
    latitude: float = 57.62,
) -> DiscoveryCard:
    return DiscoveryCard(
        provider="yandex_maps",
        provider_id=provider_id,
        name=name,
        address=address,
        longitude=longitude,
        latitude=latitude,
        rating=4.8,
        reviews_count=100,
        categories=["Ногтевая студия"],
    )


@pytest.fixture
def repository(tmp_path: Path) -> CatalogRepository:
    result = CatalogRepository(f"sqlite:///{tmp_path / 'catalog.db'}")
    result.create_schema()
    return result


def write_page(
    repository: CatalogRepository,
    category: str,
    cards: list[DiscoveryCard],
) -> str:
    spec = city_spec()
    city_id = repository.ensure_city(spec)
    category_id = repository.ensure_category(category)
    job_id = repository.prepare_job(
        city_id, category_id, spec.scope, force=True
    )
    assert repository.claim_job(job_id, "test")
    partition_id, _scope, _cursor = repository.pending_partitions(job_id)[0]
    repository.save_page(
        job_id,
        partition_id,
        category_id,
        cards,
        cursor=DiscoveryCursor(),
        next_cursor=None,
        total_hint=len(cards),
        raw_hash="page-hash",
    )
    return job_id


def test_discovery_parser_reads_only_explicit_business_items() -> None:
    payload = {
        "data": {
            "items": [
                {
                    "type": "business",
                    "id": "101",
                    "title": "Студия Лак",
                    "fullAddress": "Ярославль, улица Свободы, 1",
                    "coordinates": [39.89, 57.62],
                    "ratingData": {"ratingValue": 4.8, "reviewCount": 100},
                    "categories": [{"name": "Ногтевая студия"}],
                },
                {
                    "type": "business",
                    "id": "102",
                    "title": "Реклама",
                    "isAdvert": True,
                },
                {"type": "transit", "id": "999", "title": "Остановка"},
            ],
            "totalResultCount": 30,
            "requestContext": "ctx",
            "requestSerpId": "serp",
            "requestId": "request",
        },
        "recommendations": {
            "items": [{"type": "business", "id": "777", "title": "Не из выдачи"}]
        },
    }

    page = YandexCityDiscovery.parse_discovery_page(payload)

    assert [item.provider_id for item in page.cards] == ["101", "102"]
    assert page.cards[1].is_advert is True
    assert page.next_cursor is not None
    assert page.next_cursor.skip == 3
    assert page.next_cursor.context == "ctx"


def test_yandex_contract_drift_is_not_treated_as_empty_success() -> None:
    with pytest.raises(DiscoveryError, match="yandex_contract_missing_results"):
        YandexCityDiscovery.parse_discovery_page({"data": {"unexpected": []}})


def test_twogis_discovery_paginates_and_keeps_coordinates() -> None:
    page = TwoGisCityDiscovery.parse_discovery_page(
        {
            "result": {
                "total": 60,
                "items": [
                    {
                        "id": "7000001",
                        "name": "Студия Лак",
                        "address_name": "улица Свободы, 1",
                        "point": {"lon": 39.89, "lat": 57.62},
                        "reviews": {
                            "general_rating": 4.8,
                            "general_review_count": 100,
                        },
                        "rubrics": [{"name": "Ногтевая студия"}],
                    }
                ],
            }
        }
    )

    assert page.cards[0].provider == "2gis"
    assert page.cards[0].longitude == 39.89
    assert page.next_cursor is not None
    assert page.next_cursor.page == 1


def test_twogis_contract_error_is_not_treated_as_empty_success() -> None:
    with pytest.raises(DiscoveryError, match="twogis_contract_403"):
        TwoGisCityDiscovery.parse_discovery_page(
            {"meta": {"code": 403}, "result": {"items": []}}
        )


def test_twogis_cards_are_restricted_to_requested_scope() -> None:
    scope = city_spec().scope
    assert TwoGisCityDiscovery._inside_scope(card("inside"), scope)
    outside = card("outside", longitude=41.0, latitude=58.0)
    assert not TwoGisCityDiscovery._inside_scope(outside, scope)


def test_same_source_across_categories_is_one_location(
    repository: CatalogRepository,
) -> None:
    write_page(repository, "ногтевая студия", [card("101")])
    write_page(repository, "салон красоты", [card("101")])

    locations = repository.list_locations(city_name="Ярославль")

    assert len(locations) == 1
    assert set(locations[0]["categories"]) == {
        "ногтевая студия",
        "салон красоты",
    }


def test_branches_with_different_addresses_are_not_merged(
    repository: CatalogRepository,
) -> None:
    write_page(
        repository,
        "салон красоты",
        [
            card("101", address="Ярославль, улица Свободы, 1"),
            card(
                "102",
                address="Ярославль, Московский проспект, 100",
                longitude=39.87,
                latitude=57.58,
            ),
        ],
    )

    assert len(repository.list_locations(city_name="Ярославль")) == 2


def test_cross_source_exact_phone_links_without_coordinates(
    repository: CatalogRepository,
) -> None:
    first = card("101")
    first.phones = ["+7 999 111-22-33"]
    write_page(repository, "ногтевая студия", [first])
    second = DiscoveryCard(
        provider="2gis",
        provider_id="7001",
        name="Студия Лак",
        address="улица Свободы, 1",
        phones=["8 (999) 111-22-33"],
    )
    write_page(repository, "салон красоты", [second])

    assert len(repository.list_locations(city_name="Ярославль")) == 1


def test_same_phone_different_address_without_coordinates_keeps_branches_separate(
    repository: CatalogRepository,
) -> None:
    first = card("101", longitude=None, latitude=None)
    first.phones = ["+7 999 111-22-33"]
    write_page(repository, "ногтевая студия", [first])
    second = DiscoveryCard(
        provider="2gis",
        provider_id="7002",
        name="Студия Лак",
        address="проспект Ленина, 50",
        phones=["8 (999) 111-22-33"],
    )
    write_page(repository, "салон красоты", [second])

    assert len(repository.list_locations(city_name="Ярославль")) == 2


def test_replaying_page_does_not_duplicate_evidence_or_location(
    repository: CatalogRepository,
) -> None:
    spec = city_spec()
    city_id = repository.ensure_city(spec)
    category_id = repository.ensure_category("ногтевая студия")
    job_id = repository.prepare_job(city_id, category_id, spec.scope, force=True)
    repository.claim_job(job_id, "test")
    partition_id, _scope, _cursor = repository.pending_partitions(job_id)[0]
    kwargs = dict(
        cursor=DiscoveryCursor(),
        next_cursor=None,
        total_hint=1,
        raw_hash="stable",
    )
    repository.save_page(job_id, partition_id, category_id, [card("101")], **kwargs)
    repository.save_page(job_id, partition_id, category_id, [card("101")], **kwargs)

    summary = repository.finish_job(job_id)
    assert summary.discovered == 1
    assert summary.unique_locations == 1
    assert repository.pending_partitions(job_id) == []


def test_completed_empty_refresh_deactivates_missing_source(
    repository: CatalogRepository,
) -> None:
    first_job = write_page(repository, "ногтевая студия", [card("101")])
    repository.finish_job(first_job)
    repository.reconcile_completed_jobs([first_job])
    assert len(repository.list_locations(city_name="Ярославль")) == 1

    spec = city_spec()
    city_id = repository.ensure_city(spec)
    category_id = repository.ensure_category("ногтевая студия")
    empty_job = repository.prepare_job(city_id, category_id, spec.scope, force=True)
    assert repository.claim_job(empty_job, "test")
    partition_id, _scope, cursor = repository.pending_partitions(empty_job)[0]
    repository.save_page(
        empty_job,
        partition_id,
        category_id,
        [],
        cursor=cursor,
        next_cursor=None,
        total_hint=0,
        raw_hash="empty",
    )
    repository.finish_job(empty_job)
    repository.reconcile_completed_jobs([empty_job])

    assert repository.list_locations(city_name="Ярославль") == []


def test_profile_snapshots_only_change_with_content(
    repository: CatalogRepository,
) -> None:
    write_page(repository, "ногтевая студия", [card("101")])
    location_id = repository.list_locations()[0]["id"]
    profile = SalonProfile(
        provider="yandex_maps",
        provider_id="101",
        name="Студия Лак",
        address="Ярославль, улица Свободы, 1",
        rating=4.8,
        reviews=[Review(author="Анна", text="Отлично", rating=5)],
        services=[Service(name="Маникюр", price="1500 ₽")],
    )

    assert repository.save_profile(location_id, profile) is True
    assert repository.save_profile(location_id, profile) is False
    profile.collected_at = datetime.now(UTC) + timedelta(hours=1)
    assert repository.save_profile(location_id, profile) is False
    assert repository.pending_yandex_cards(refresh_hours=24) == []
    profile.rating = 4.9
    assert repository.save_profile(location_id, profile) is True
    assert repository.get_location(location_id)["profile"]["rating"] == 4.9


def test_failed_detail_card_is_backed_off_so_queue_can_progress(
    repository: CatalogRepository,
) -> None:
    write_page(repository, "ногтевая студия", [card("101"), card("102")])
    first = repository.pending_yandex_cards(limit=1)[0]

    repository.record_detail_failure(first["provider_id"], "TimeoutError")

    second = repository.pending_yandex_cards(limit=1)[0]
    assert second["provider_id"] != first["provider_id"]


def test_competitors_are_deterministic_and_require_category_overlap() -> None:
    locations = [
        {
            "id": "a",
            "categories": {"ногтевая студия"},
            "services": {"маникюр"},
            "latitude": 57.62,
            "longitude": 39.89,
            "rating": 4.8,
            "reviews_count": 100,
        },
        {
            "id": "b",
            "categories": {"ногтевая студия", "салон красоты"},
            "services": {"маникюр", "педикюр"},
            "latitude": 57.621,
            "longitude": 39.891,
            "rating": 4.7,
            "reviews_count": 80,
        },
        {
            "id": "c",
            "categories": {"автосервис"},
            "services": set(),
            "latitude": 57.621,
            "longitude": 39.891,
        },
    ]

    first = compute_competitors(locations)
    second = compute_competitors(locations)

    assert first == second
    assert {(item.location_id, item.competitor_id) for item in first} == {
        ("a", "b"),
        ("b", "a"),
    }
    assert compare_locations(locations[0], locations[2]) is None


def test_empty_competitor_recalculation_removes_stale_edges(
    repository: CatalogRepository,
) -> None:
    write_page(
        repository,
        "ногтевая студия",
        [
            card("101"),
            card(
                "102",
                name="Ногтевая студия Блик",
                address="Ярославль, улица Свободы, 3",
                longitude=39.891,
                latitude=57.621,
            ),
        ],
    )
    features = repository.location_features("Ярославль")
    matches = compute_competitors(features)
    location_ids = [feature["id"] for feature in features]
    repository.replace_competitors(matches, location_ids)
    assert repository.competitors(location_ids[0])

    repository.replace_competitors([], location_ids)

    assert repository.competitors(location_ids[0]) == []


class FakeDiscovery:
    def __init__(self) -> None:
        self.calls: defaultdict[str, int] = defaultdict(int)

    async def fetch_page(self, _city, _query, scope, cursor) -> DiscoveryPage:
        self.calls[scope.key] += 1
        return DiscoveryPage(
            cards=[card(f"{scope.key.replace('.', '')}{cursor.page + 1}".replace("root", "9"))],
            cursor=cursor,
            next_cursor=(
                DiscoveryCursor(page=1, skip=1) if cursor.page == 0 else None
            ),
            total_hint=2,
            raw_hash=f"{scope.key}-{cursor.page}",
        )


class FakeDetail:
    async def collect_by_id(self, provider_id: str) -> SalonProfile:
        return SalonProfile(
            provider="yandex_maps",
            provider_id=provider_id,
            name="Студия Лак",
            rating=4.8,
        )


class BrokenDiscovery:
    async def fetch_page(self, *_args) -> DiscoveryPage:
        raise RuntimeError("transport implementation crashed")


@pytest.mark.asyncio
async def test_unexpected_discovery_failure_becomes_retryable_partial_job(
    repository: CatalogRepository,
) -> None:
    service = CityCatalogService(
        repository,
        BrokenDiscovery(),  # type: ignore[arg-type]
        FakeDetail(),  # type: ignore[arg-type]
    )

    result = await service.crawl_city(city_spec(), ["ногтевая студия"], force=True)

    assert result[0].status == "partial"
    assert result[0].partitions_failed == 1


@pytest.mark.asyncio
async def test_city_service_paginates_and_reuses_completed_weekly_job(
    repository: CatalogRepository,
) -> None:
    discovery = FakeDiscovery()
    service = CityCatalogService(
        repository,
        discovery,  # type: ignore[arg-type]
        FakeDetail(),  # type: ignore[arg-type]
        max_pages=3,
    )

    first = await service.crawl_city(city_spec(), ["ногтевая студия"])
    second = await service.crawl_city(city_spec(), ["ногтевая студия"])

    assert first[0].status == "completed"
    assert first[0].discovered == 2
    assert second[0].job_id == first[0].job_id
    assert discovery.calls["root"] == 2
