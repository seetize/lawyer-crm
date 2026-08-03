from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PartitionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    EMPTY = "empty"
    SPLIT = "split"
    BLOCKED = "blocked"
    FAILED = "failed"
    TRUNCATED = "truncated"


class DiscoveryScope(BaseModel):
    west: float
    south: float
    east: float
    north: float
    depth: int = 0
    key: str = "root"

    @property
    def center(self) -> tuple[float, float]:
        return (self.west + self.east) / 2, (self.south + self.north) / 2

    @property
    def span(self) -> tuple[float, float]:
        return self.east - self.west, self.north - self.south

    def split(self) -> list["DiscoveryScope"]:
        center_lon, center_lat = self.center
        cells = (
            (self.west, self.south, center_lon, center_lat, "sw"),
            (center_lon, self.south, self.east, center_lat, "se"),
            (self.west, center_lat, center_lon, self.north, "nw"),
            (center_lon, center_lat, self.east, self.north, "ne"),
        )
        return [
            DiscoveryScope(
                west=west,
                south=south,
                east=east,
                north=north,
                depth=self.depth + 1,
                key=f"{self.key}.{suffix}",
            )
            for west, south, east, north, suffix in cells
        ]


class CitySpec(BaseModel):
    name: str
    country_code: str = "RU"
    timezone: str = "Europe/Moscow"
    yandex_geo_id: str = "0"
    scope: DiscoveryScope


class DiscoveryCursor(BaseModel):
    page: int = 0
    skip: int = 0
    context: str | None = None
    serp_id: str | None = None
    parent_request_id: str | None = None


class DiscoveryCard(BaseModel):
    provider: str
    provider_id: str
    name: str
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    phones: list[str] = Field(default_factory=list)
    website_domain: str | None = None
    booking_identity: str | None = None
    categories: list[str] = Field(default_factory=list)
    district: str | None = None
    metro_stations: list[str] = Field(default_factory=list)
    rating: float | None = None
    reviews_count: int | None = None
    source_url: str | None = None
    is_advert: bool = False
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DiscoveryPage(BaseModel):
    cards: list[DiscoveryCard]
    cursor: DiscoveryCursor
    next_cursor: DiscoveryCursor | None = None
    total_hint: int | None = None
    raw_hash: str
    blocked: bool = False
    repeated: bool = False


class CrawlSummary(BaseModel):
    job_id: str
    city: str
    category: str
    status: JobStatus
    discovered: int = 0
    unique_locations: int = 0
    partitions_completed: int = 0
    partitions_failed: int = 0
    coverage_uncertain: bool = False


class CompetitorMatch(BaseModel):
    location_id: str
    competitor_id: str
    score: float
    distance_km: float | None = None
    reasons: list[str] = Field(default_factory=list)
