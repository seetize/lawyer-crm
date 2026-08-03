from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.catalog.competitors import compare_locations, compute_competitors
from app.catalog.comparison import build_comparison_report
from app.catalog.areas import DistrictBoundary, OpenStreetMapDistrictResolver, assign_districts
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
                    "addressComponents": [
                        {"kind": "locality", "name": "Ярославль"},
                        {"kind": "district", "name": "Кировский район"},
                    ],
                    "metro": [{"name": "Площадь Ленина"}],
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
    assert page.cards[0].district == "Кировский район"
    assert page.cards[0].metro_stations == ["Площадь Ленина"]


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
    active = repository.list_locations(city_name="Ярославль")
    assert len(active) == 1
    location_id = active[0]["id"]

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
    assert repository.get_location(location_id) is None


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


def test_catalog_categories_and_zones_filter_saved_locations_without_duplicates(
    repository: CatalogRepository,
) -> None:
    write_page(repository, "салон красоты", [card("101")])
    write_page(repository, "ногтевая студия", [card("101")])
    location = repository.list_locations(city_name="Ярославль")[0]
    profile = SalonProfile(
        provider="yandex_maps",
        provider_id="101",
        name="Студия Лак",
        district="Кировский район",
        metro_stations=["Тверская", "Пушкинская"],
        rating=4.8,
    )
    repository.save_profile(location["id"], profile)

    categories = repository.list_categories("Ярославль", "салон красоты")
    zones = repository.list_zones(
        "Ярославль", "district", category_id=categories[0]["id"]
    )
    results = repository.list_locations(
        city_name="Ярославль",
        category_id=categories[0]["id"],
        zone_type="district",
        zone_name="  КИРОВСКИЙ   РАЙОН ",
    )

    assert categories[0]["count"] == 1
    assert zones == [{"name": "Кировский район", "count": 1}]
    assert len(results) == 1
    assert results[0]["metros"] == ["Тверская", "Пушкинская"]

    assert repository.backfill_profile_areas() == 0
    assert repository.backfill_profile_areas() == 0
    assert repository.list_zones("Ярославль", "metro") == [
        {"name": "Пушкинская", "count": 1},
        {"name": "Тверская", "count": 1},
    ]


def test_discovery_areas_are_searchable_before_profile_enrichment(
    repository: CatalogRepository,
) -> None:
    discovered = card("district-card").model_copy(
        update={
            "district": "Кировский район",
            "metro_stations": ["Площадь Ленина"],
        }
    )
    write_page(repository, "салон красоты", [discovered])
    category = repository.list_categories("Ярославль", "салон красоты")[0]

    assert repository.list_zones(
        "Ярославль", "district", category_id=category["id"]
    ) == [{"name": "Кировский район", "count": 1}]
    assert len(
        repository.list_locations(
            city_name="Ярославль",
            category_id=category["id"],
            zone_type="district",
            zone_name="кировский район",
        )
    ) == 1


def test_radius_search_is_distance_sorted_and_category_scoped(
    repository: CatalogRepository,
) -> None:
    write_page(
        repository,
        "салон красоты",
        [
            card("center", name="Центр", latitude=57.62),
            card("near", name="Рядом", latitude=57.624),
            card("outside", name="Далеко", latitude=57.64),
        ],
    )
    category = repository.list_categories("Ярославль", "салон красоты")[0]

    nearby = repository.list_locations(
        city_name="Ярославль",
        category_id=category["id"],
        center_latitude=57.62,
        center_longitude=39.89,
        radius_km=1,
    )

    assert [item["name"] for item in nearby] == ["Центр", "Рядом"]
    assert nearby[0]["distance_km"] == 0
    with pytest.raises(ValueError, match="Radius search requires"):
        repository.list_locations(
            city_name="Ярославль",
            center_latitude=57.62,
            center_longitude=39.89,
            radius_km=2,
        )


def test_boundary_districts_are_assigned_from_saved_coordinates(
    repository: CatalogRepository,
) -> None:
    write_page(repository, "салон красоты", [card("inside"), card("outside", longitude=40.0)])
    locations = repository.coordinate_locations("Ярославль")
    boundary = DistrictBoundary(
        name="Кировский район",
        polygons=(
            (
                (39.8, 57.5),
                (39.95, 57.5),
                (39.95, 57.7),
                (39.8, 57.7),
            ),
        ),
    )

    assignments = assign_districts(locations, [boundary])
    assigned = repository.replace_boundary_districts("Ярославль", assignments)

    assert assigned == 1
    assert repository.list_zones("Ярославль", "district") == [
        {"name": "Кировский район", "count": 1}
    ]


def test_yandex_area_evidence_outlives_osm_refresh(repository: CatalogRepository) -> None:
    discovered = card("area-owner").model_copy(update={"district": "Кировский район"})
    write_page(repository, "салон красоты", [discovered])
    location = repository.list_locations(city_name="Ярославль")[0]

    repository.replace_boundary_districts(
        "Ярославль", {location["id"]: "Кировский район"}
    )
    repository.replace_boundary_districts("Ярославль", {})

    assert repository.list_zones("Ярославль", "district") == [
        {"name": "Кировский район", "count": 1}
    ]


@pytest.mark.asyncio
async def test_empty_boundary_response_preserves_saved_districts(
    repository: CatalogRepository,
) -> None:
    write_page(repository, "салон красоты", [card("saved-boundary")])
    location = repository.list_locations(city_name="Ярославль")[0]
    repository.replace_boundary_districts(
        "Ярославль", {location["id"]: "Кировский район"}
    )

    class EmptyResolver(OpenStreetMapDistrictResolver):
        async def resolve(self, city: CitySpec) -> list[DistrictBoundary]:
            return []

    with pytest.raises(RuntimeError, match="No validated"):
        await EmptyResolver().refresh(repository, city_spec())
    assert repository.list_zones("Ярославль", "district") == [
        {"name": "Кировский район", "count": 1}
    ]


def test_reconcile_one_provider_does_not_hide_other_provider_category(
    repository: CatalogRepository,
) -> None:
    yandex_job = write_page(repository, "салон красоты", [card("101")])
    repository.finish_job(yandex_job)
    repository.reconcile_completed_jobs([yandex_job])
    spec = city_spec()
    city_id = repository.ensure_city(spec)
    category_id = repository.ensure_category("салон красоты")
    twogis_job = repository.prepare_job(
        city_id,
        category_id,
        spec.scope,
        provider="2gis",
        force=True,
    )
    assert repository.claim_job(twogis_job, "test")
    partition_id, _scope, cursor = repository.pending_partitions(twogis_job)[0]
    repository.save_page(
        twogis_job,
        partition_id,
        category_id,
        [],
        cursor=cursor,
        next_cursor=None,
        total_hint=0,
        raw_hash="empty-2gis",
    )
    repository.finish_job(twogis_job)
    repository.reconcile_completed_jobs([twogis_job])

    categories = repository.list_categories("Ярославль", "салон красоты")
    assert categories[0]["count"] == 1


def test_local_comparison_report_uses_saved_evidence() -> None:
    selected = {
        "id": "a",
        "name": "Студия А",
        "categories": {"ногтевая студия"},
        "services": {"маникюр", "педикюр"},
        "rating": 4.9,
        "reviews_count": 200,
        "latitude": 57.62,
        "longitude": 39.89,
        "review_texts": ["Отличное качество работ и аккуратный мастер"],
    }
    competitor = {
        "id": "b",
        "name": "Студия Б",
        "categories": {"ногтевая студия"},
        "services": {"маникюр"},
        "rating": 4.5,
        "reviews_count": 50,
        "latitude": 57.621,
        "longitude": 39.891,
        "review_texts": ["Долго ждала, цена высокая"],
    }

    report = build_comparison_report(selected, [selected, competitor], "весь город")

    assert "Студия Б" in report
    assert "рейтинг ниже на 0.4" in report
    assert "меньше подтверждений отзывами: 50 против 200" in report
    assert "в единичных отзывах упоминаются: ожидание, цены" in report
    assert "новый парсинг при нажатии не запускается" in report


def test_comparison_does_not_treat_missing_services_as_zero_strength() -> None:
    selected = {
        "id": "a",
        "name": "Точка",
        "categories": {"салон красоты"},
        "services": set(),
        "rating": 4.8,
        "reviews_count": 20,
        "latitude": 57.62,
        "longitude": 39.89,
    }
    competitor = {
        **selected,
        "id": "b",
        "name": "Конкурент",
        "latitude": 57.621,
    }

    report = build_comparison_report(selected, [selected, competitor], "радиус 1 км")

    assert "Не оценивалось: ассортимент услуг — нет сопоставимых публичных прайсов" in report
    assert "ассортимент услуг не уже" not in report


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
