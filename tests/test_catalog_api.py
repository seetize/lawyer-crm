from pathlib import Path

from fastapi.testclient import TestClient

from app.api import app, get_catalog_repository
from app.catalog.db import CatalogRepository
from app.catalog.domain import CitySpec, DiscoveryCard, DiscoveryCursor, DiscoveryScope


def populated_repository(tmp_path: Path) -> CatalogRepository:
    repository = CatalogRepository(f"sqlite:///{tmp_path / 'api.db'}")
    repository.create_schema()
    city = CitySpec(
        name="Ярославль",
        yandex_geo_id="16",
        scope=DiscoveryScope(west=39.7, south=57.5, east=40.05, north=57.75),
    )
    city_id = repository.ensure_city(city)
    category_id = repository.ensure_category("ногтевая студия")
    job_id = repository.prepare_job(city_id, category_id, city.scope, force=True)
    repository.claim_job(job_id, "api-test")
    partition_id, _scope, _cursor = repository.pending_partitions(job_id)[0]
    repository.save_page(
        job_id,
        partition_id,
        category_id,
        [
            DiscoveryCard(
                provider="yandex_maps",
                provider_id="101",
                name="Студия Лак",
                address="Ярославль, улица Свободы, 1",
                longitude=39.89,
                latitude=57.62,
            )
        ],
        cursor=DiscoveryCursor(),
        next_cursor=None,
        total_hint=1,
        raw_hash="one",
    )
    repository.finish_job(job_id)
    return repository


def test_catalog_read_api_and_readiness(tmp_path: Path) -> None:
    repository = populated_repository(tmp_path)
    app.dependency_overrides[get_catalog_repository] = lambda: repository
    try:
        client = TestClient(app)
        ready = client.get("/health/ready")
        locations = client.get(
            "/v1/locations", params={"city": "Ярославль", "query": "Лак"}
        )
        nearby = client.get(
            "/v1/locations",
            params={
                "city": "Ярославль",
                "center_latitude": 57.62,
                "center_longitude": 39.89,
                "radius_km": 1,
            },
        )
        invalid_radius = client.get(
            "/v1/locations",
            params={"city": "Ярославль", "radius_km": 2},
        )
        status = client.get("/v1/catalog/status", params={"city": "Ярославль"})
        location_id = locations.json()[0]["id"]
        passport = client.get(f"/v1/locations/{location_id}")
        competitors = client.get(f"/v1/locations/{location_id}/competitors")
    finally:
        app.dependency_overrides.clear()

    assert ready.status_code == 200
    assert locations.status_code == 200
    assert len(locations.json()) == 1
    assert nearby.status_code == 200
    assert nearby.json()[0]["distance_km"] == 0
    assert invalid_radius.status_code == 422
    assert status.json()["locations"] == 1
    assert passport.json()["name"] == "Студия Лак"
    assert competitors.json() == []


def test_unknown_catalog_location_is_404(tmp_path: Path) -> None:
    repository = populated_repository(tmp_path)
    app.dependency_overrides[get_catalog_repository] = lambda: repository
    try:
        response = TestClient(app).get("/v1/locations/not-found")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
