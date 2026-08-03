from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    inspect,
    or_,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.catalog.domain import (
    CitySpec,
    CompetitorMatch,
    CrawlSummary,
    DiscoveryCard,
    DiscoveryCursor,
    DiscoveryScope,
    JobStatus,
    PartitionStatus,
)
from app.models import SalonProfile


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class CityRow(Base):
    __tablename__ = "catalog_cities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2))
    name: Mapped[str] = mapped_column(String(160))
    normalized_name: Mapped[str] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(80))
    west: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    yandex_geo_id: Mapped[str] = mapped_column(String(32), default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (UniqueConstraint("country_code", "normalized_name"),)


class CategoryRow(Base):
    __tablename__ = "catalog_categories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AreaRow(Base):
    __tablename__ = "catalog_areas"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("catalog_cities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    source: Mapped[str] = mapped_column(String(40), default="yandex_profile")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (UniqueConstraint("city_id", "kind", "normalized_name"),)


class LocationRow(Base):
    __tablename__ = "catalog_locations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("catalog_cities.id"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    canonical_address: Mapped[str | None] = mapped_column(String(500))
    normalized_address: Mapped[str | None] = mapped_column(String(500), index=True)
    longitude: Mapped[float | None] = mapped_column(Float, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, index=True)
    website_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    booking_identity: Mapped[str | None] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    profile_hash: Mapped[str | None] = mapped_column(String(64))
    profile_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    area_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    completeness: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SourceCardRow(Base):
    __tablename__ = "catalog_source_cards"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_object_id: Mapped[str] = mapped_column(String(160))
    location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    normalized_address: Mapped[str | None] = mapped_column(String(500), index=True)
    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    phones_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    website_domain: Mapped[str | None] = mapped_column(String(255))
    booking_identity: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    detail_failures: Mapped[int] = mapped_column(Integer, default=0)
    detail_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    detail_error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("provider", "provider_object_id"),)


class LocationCategoryRow(Base):
    __tablename__ = "catalog_location_categories"
    location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), primary_key=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("catalog_categories.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LocationAreaRow(Base):
    __tablename__ = "catalog_location_areas"
    location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), primary_key=True)
    area_id: Mapped[str] = mapped_column(ForeignKey("catalog_areas.id"), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40), default="yandex_profile")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CrawlJobRow(Base):
    __tablename__ = "catalog_crawl_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("catalog_cities.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("catalog_categories.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="yandex_maps")
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_fingerprint: Mapped[str | None] = mapped_column(String(64))
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("attempt >= 0 AND attempt <= max_attempts"),
        Index("ix_catalog_job_ready", "status", "lease_expires_at"),
    )


class CrawlPartitionRow(Base):
    __tablename__ = "catalog_crawl_partitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("catalog_crawl_jobs.id"), index=True)
    key: Mapped[str] = mapped_column(String(255))
    depth: Mapped[int] = mapped_column(Integer)
    west: Mapped[float] = mapped_column(Float)
    south: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default=PartitionStatus.QUEUED, index=True)
    cursor_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    page_number: Mapped[int] = mapped_column(Integer, default=0)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    total_hint: Mapped[int | None] = mapped_column(Integer)
    last_raw_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (UniqueConstraint("job_id", "key"),)


class DiscoveryEvidenceRow(Base):
    __tablename__ = "catalog_discovery_evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("catalog_crawl_jobs.id"), index=True)
    partition_id: Mapped[str] = mapped_column(ForeignKey("catalog_crawl_partitions.id"), index=True)
    source_card_id: Mapped[str] = mapped_column(ForeignKey("catalog_source_cards.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("catalog_categories.id"), index=True)
    organic_position: Mapped[int | None] = mapped_column(Integer)
    is_advert: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        UniqueConstraint("job_id", "partition_id", "source_card_id", "category_id"),
    )


class PassportSnapshotRow(Base):
    __tablename__ = "catalog_passport_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    __table_args__ = (
        UniqueConstraint("location_id", "schema_version", "content_hash"),
    )


class MergeCandidateRow(Base):
    __tablename__ = "catalog_merge_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    left_location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), index=True)
    right_location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="candidate")
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    algorithm_version: Mapped[str] = mapped_column(String(40), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("left_location_id <> right_location_id"),
        UniqueConstraint("left_location_id", "right_location_id", "algorithm_version"),
    )


class CompetitorRow(Base):
    __tablename__ = "catalog_competitors"
    location_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), primary_key=True)
    competitor_id: Mapped[str] = mapped_column(ForeignKey("catalog_locations.id"), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float | None] = mapped_column(Float)
    reasons_json: Mapped[list[str]] = mapped_column(JSON)
    algorithm_version: Mapped[str] = mapped_column(String(40), default="v1")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CatalogRepository:
    """Durable, idempotent catalogue storage.

    SQLite is a local QA fallback. Production uses PostgreSQL through the same
    SQLAlchemy contract; distributed workers must only be enabled on PostgreSQL.
    """

    def __init__(self, database_url: str, snapshot_limit: int = 3) -> None:
        self.database_url = database_url
        self.snapshot_limit = max(1, snapshot_limit)
        if database_url.startswith("sqlite:///"):
            path = Path(database_url.removeprefix("sqlite:///"))
            if not path.is_absolute():
                path = Path.cwd() / path
            path.parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def health(self) -> bool:
        with self.engine.connect() as connection:
            connection.execute(select(1))
        required = {CityRow.__tablename__, LocationRow.__tablename__, CrawlJobRow.__tablename__}
        if not required.issubset(set(inspect(self.engine).get_table_names())):
            raise RuntimeError("Catalog schema is not migrated")
        return True

    def ensure_city(self, spec: CitySpec) -> str:
        normalized = normalize_text(spec.name)
        with Session(self.engine) as session, session.begin():
            row = session.scalar(
                select(CityRow).where(
                    CityRow.country_code == spec.country_code,
                    CityRow.normalized_name == normalized,
                )
            )
            if row is None:
                row = CityRow(
                    id=str(uuid.uuid4()),
                    country_code=spec.country_code,
                    name=spec.name,
                    normalized_name=normalized,
                    timezone=spec.timezone,
                    west=spec.scope.west,
                    south=spec.scope.south,
                    east=spec.scope.east,
                    north=spec.scope.north,
                    yandex_geo_id=spec.yandex_geo_id,
                )
                session.add(row)
            else:
                row.west, row.south, row.east, row.north = (
                    spec.scope.west,
                    spec.scope.south,
                    spec.scope.east,
                    spec.scope.north,
                )
                row.yandex_geo_id = spec.yandex_geo_id
                row.updated_at = utc_now()
            return row.id

    def ensure_category(self, query: str) -> str:
        key = normalize_text(query)
        with Session(self.engine) as session, session.begin():
            row = session.scalar(select(CategoryRow).where(CategoryRow.key == key))
            if row is None:
                row = CategoryRow(id=str(uuid.uuid4()), key=key, display_name=query)
                session.add(row)
            return row.id

    def prepare_job(
        self,
        city_id: str,
        category_id: str,
        scope: DiscoveryScope,
        *,
        provider: str = "yandex_maps",
        force: bool = False,
        refresh_hours: int = 168,
    ) -> str:
        bucket_seconds = timedelta(hours=max(1, refresh_hours)).total_seconds()
        period = str(int(utc_now().timestamp() // bucket_seconds))
        key = f"{provider}:{city_id}:{category_id}:{period}"
        if force:
            key += f":{uuid.uuid4()}"
        with Session(self.engine) as session, session.begin():
            job = session.scalar(
                select(CrawlJobRow).where(CrawlJobRow.idempotency_key == key)
            )
            if job is None:
                job = CrawlJobRow(
                    id=str(uuid.uuid4()),
                    idempotency_key=key,
                    city_id=city_id,
                    category_id=category_id,
                    provider=provider,
                    status=JobStatus.QUEUED,
                )
                session.add(job)
                session.flush()
                session.add(self._partition_row(job.id, scope))
            return job.id

    @staticmethod
    def _partition_row(job_id: str, scope: DiscoveryScope) -> CrawlPartitionRow:
        return CrawlPartitionRow(
            id=str(uuid.uuid4()),
            job_id=job_id,
            key=scope.key,
            depth=scope.depth,
            west=scope.west,
            south=scope.south,
            east=scope.east,
            north=scope.north,
            status=PartitionStatus.QUEUED,
            cursor_json=DiscoveryCursor().model_dump(),
        )

    def claim_job(self, job_id: str, owner: str, lease_minutes: int = 15) -> bool:
        with Session(self.engine) as session, session.begin():
            job = session.scalar(
                select(CrawlJobRow)
                .where(CrawlJobRow.id == job_id)
                .with_for_update()
            )
            if job is None or job.status in {
                JobStatus.COMPLETED,
                JobStatus.CANCELLED,
            }:
                return False
            now = utc_now()
            if job.lease_expires_at and _aware(job.lease_expires_at) > now and job.lease_owner != owner:
                return False
            if job.attempt >= job.max_attempts:
                return False
            job.status = JobStatus.RUNNING
            job.attempt += 1
            job.lease_owner = owner
            job.lease_expires_at = now + timedelta(minutes=lease_minutes)
            job.updated_at = now
            return True

    def pending_partitions(self, job_id: str) -> list[tuple[str, DiscoveryScope, DiscoveryCursor]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CrawlPartitionRow)
                .where(
                    CrawlPartitionRow.job_id == job_id,
                    CrawlPartitionRow.status.in_(
                        [PartitionStatus.QUEUED, PartitionStatus.RUNNING, PartitionStatus.FAILED]
                    ),
                )
                .order_by(CrawlPartitionRow.depth, CrawlPartitionRow.key)
            ).all()
            return [
                (
                    row.id,
                    DiscoveryScope(
                        west=row.west,
                        south=row.south,
                        east=row.east,
                        north=row.north,
                        depth=row.depth,
                        key=row.key,
                    ),
                    DiscoveryCursor.model_validate(row.cursor_json or {}),
                )
                for row in rows
            ]

    def save_page(
        self,
        job_id: str,
        partition_id: str,
        category_id: str,
        cards: Iterable[DiscoveryCard],
        *,
        cursor: DiscoveryCursor,
        next_cursor: DiscoveryCursor | None,
        total_hint: int | None,
        raw_hash: str,
    ) -> int:
        observed = list(cards)
        with Session(self.engine) as session, session.begin():
            job = session.get(CrawlJobRow, job_id)
            partition = session.get(CrawlPartitionRow, partition_id)
            if job is None or partition is None:
                raise ValueError("Unknown crawl job or partition")
            city_id = job.city_id
            organic_position = partition.result_count
            for card in observed:
                location, source = self._upsert_card(session, city_id, card)
                self._replace_discovery_areas(session, location, card)
                membership_ids = [category_id]
                for discovered_category in card.categories:
                    key = normalize_text(discovered_category)
                    category = session.scalar(
                        select(CategoryRow).where(CategoryRow.key == key)
                    )
                    if category is None:
                        category = CategoryRow(
                            id=str(uuid.uuid4()),
                            key=key,
                            display_name=discovered_category,
                        )
                        session.add(category)
                        session.flush()
                    membership_ids.append(category.id)
                for membership_id in dict.fromkeys(membership_ids):
                    membership = session.get(
                        LocationCategoryRow,
                        {"location_id": location.id, "category_id": membership_id},
                    )
                    if membership is None:
                        session.add(
                            LocationCategoryRow(
                                location_id=location.id,
                                category_id=membership_id,
                            )
                        )
                    else:
                        membership.last_seen_at = utc_now()
                        membership.active = True
                evidence = session.scalar(
                    select(DiscoveryEvidenceRow).where(
                        DiscoveryEvidenceRow.job_id == job_id,
                        DiscoveryEvidenceRow.partition_id == partition_id,
                        DiscoveryEvidenceRow.source_card_id == source.id,
                        DiscoveryEvidenceRow.category_id == category_id,
                    )
                )
                if evidence is None:
                    if not card.is_advert:
                        organic_position += 1
                    session.add(
                        DiscoveryEvidenceRow(
                            id=str(uuid.uuid4()),
                            job_id=job_id,
                            partition_id=partition_id,
                            source_card_id=source.id,
                            category_id=category_id,
                            organic_position=(organic_position if not card.is_advert else None),
                            is_advert=card.is_advert,
                        )
                    )
            partition.cursor_json = (
                next_cursor.model_dump(mode="json") if next_cursor else cursor.model_dump(mode="json")
            )
            partition.page_number = cursor.page
            partition.result_count = organic_position
            partition.total_hint = total_hint
            partition.last_raw_hash = raw_hash
            partition.status = PartitionStatus.RUNNING if next_cursor else (
                PartitionStatus.COMPLETED if observed else PartitionStatus.EMPTY
            )
            partition.updated_at = utc_now()
            job.discovered_count = int(
                session.scalar(
                    select(func.count(func.distinct(SourceCardRow.id)))
                    .select_from(DiscoveryEvidenceRow)
                    .join(SourceCardRow, SourceCardRow.id == DiscoveryEvidenceRow.source_card_id)
                    .where(DiscoveryEvidenceRow.job_id == job_id)
                )
                or 0
            )
            job.updated_at = utc_now()
            return len(observed)

    def _upsert_card(
        self,
        session: Session,
        city_id: str,
        card: DiscoveryCard,
    ) -> tuple[LocationRow, SourceCardRow]:
        source = session.scalar(
            select(SourceCardRow).where(
                SourceCardRow.provider == card.provider,
                SourceCardRow.provider_object_id == card.provider_id,
            )
        )
        content = card.model_dump(mode="json", exclude={"observed_at"})
        content_hash = stable_hash(content)
        if source is not None:
            location = session.get(LocationRow, source.location_id)
            if location is None:
                raise ValueError("Source card points to a missing location")
            changed = source.content_hash != content_hash
            source.name = card.name
            source.normalized_name = normalize_text(card.name)
            source.address = card.address
            source.normalized_address = normalize_text(card.address or "") or None
            source.longitude, source.latitude = card.longitude, card.latitude
            source.phones_json = normalized_phones(card.phones)
            source.website_domain = card.website_domain
            source.booking_identity = card.booking_identity
            source.rating, source.reviews_count = card.rating, card.reviews_count
            source.source_url = card.source_url
            source.content_hash = content_hash
            source.last_seen_at = utc_now()
            source.active = True
            if changed:
                source.last_changed_at = utc_now()
            if card.provider == "yandex_maps":
                location.canonical_name = card.name
                location.normalized_name = normalize_text(card.name)
                location.canonical_address = card.address
                location.normalized_address = normalize_text(card.address or "") or None
                location.longitude, location.latitude = card.longitude, card.latitude
                location.website_domain = card.website_domain
                location.booking_identity = card.booking_identity
            location.last_seen_at = utc_now()
            return location, source

        location, score, ambiguous = self._resolve_location(session, city_id, card)
        if location is None:
            location = LocationRow(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"catalog:{city_id}:{card.provider}:{card.provider_id}")),
                city_id=city_id,
                canonical_name=card.name,
                normalized_name=normalize_text(card.name),
                canonical_address=card.address,
                normalized_address=normalize_text(card.address or "") or None,
                longitude=card.longitude,
                latitude=card.latitude,
                website_domain=card.website_domain,
                booking_identity=card.booking_identity,
            )
            session.add(location)
            session.flush()
            if ambiguous is not None:
                left, right = sorted((location.id, ambiguous.id))
                session.add(
                    MergeCandidateRow(
                        id=str(uuid.uuid4()),
                        left_location_id=left,
                        right_location_id=right,
                        score=score,
                        evidence_json={"kind": "name_address_similarity"},
                    )
                )
        source = SourceCardRow(
            id=str(uuid.uuid4()),
            provider=card.provider,
            provider_object_id=card.provider_id,
            location_id=location.id,
            source_url=card.source_url,
            name=card.name,
            normalized_name=normalize_text(card.name),
            address=card.address,
            normalized_address=normalize_text(card.address or "") or None,
            longitude=card.longitude,
            latitude=card.latitude,
            phones_json=normalized_phones(card.phones),
            website_domain=card.website_domain,
            booking_identity=card.booking_identity,
            rating=card.rating,
            reviews_count=card.reviews_count,
            content_hash=content_hash,
        )
        session.add(source)
        session.flush()
        return location, source

    def _resolve_location(
        self,
        session: Session,
        city_id: str,
        card: DiscoveryCard,
    ) -> tuple[LocationRow | None, float, LocationRow | None]:
        name = normalize_text(card.name)
        address = normalize_text(card.address or "")
        identity_candidate = None
        if card.website_domain or card.booking_identity:
            identity_candidate = session.scalar(
                select(LocationRow).where(
                    LocationRow.city_id == city_id,
                    or_(
                        LocationRow.website_domain == card.website_domain
                        if card.website_domain
                        else False,
                        LocationRow.booking_identity == card.booking_identity
                        if card.booking_identity
                        else False,
                    ),
                    LocationRow.status == "active",
                )
            )
        phone_values = set(normalized_phones(card.phones))
        if identity_candidate is None and phone_values:
            source_rows = session.execute(
                select(SourceCardRow, LocationRow)
                .join(LocationRow, LocationRow.id == SourceCardRow.location_id)
                .where(LocationRow.city_id == city_id, LocationRow.status == "active")
            ).all()
            identity_candidate = next(
                (
                    location
                    for source, location in source_rows
                    if phone_values & set(source.phones_json or [])
                ),
                None,
            )
        if identity_candidate is not None:
            distance = haversine_km(
                card.latitude,
                card.longitude,
                identity_candidate.latitude,
                identity_candidate.longitude,
            )
            same_address = addresses_equivalent(
                address,
                identity_candidate.normalized_address or "",
            )
            if same_address or (not math.isinf(distance) and distance <= 0.5):
                return identity_candidate, 1.0, None
            return None, 0.8, identity_candidate
        strong = session.scalar(
            select(LocationRow).where(
                LocationRow.city_id == city_id,
                LocationRow.normalized_name == name,
                LocationRow.normalized_address == (address or None),
                LocationRow.status == "active",
            )
        )
        if strong is not None and address:
            return strong, 1.0, None
        if card.longitude is None or card.latitude is None:
            return None, 0.0, None
        delta = 0.003
        candidates = session.scalars(
            select(LocationRow).where(
                LocationRow.city_id == city_id,
                LocationRow.longitude.between(card.longitude - delta, card.longitude + delta),
                LocationRow.latitude.between(card.latitude - delta, card.latitude + delta),
                LocationRow.status == "active",
            )
        ).all()
        scored: list[tuple[float, LocationRow]] = []
        for candidate in candidates:
            distance = haversine_km(
                card.latitude,
                card.longitude,
                candidate.latitude,
                candidate.longitude,
            )
            similarity = SequenceMatcher(None, name, candidate.normalized_name).ratio()
            address_similarity = SequenceMatcher(
                None, address, candidate.normalized_address or ""
            ).ratio()
            score = similarity * 0.55 + address_similarity * 0.25 + max(0, 1 - distance / 0.3) * 0.2
            scored.append((score, candidate))
        scored.sort(key=lambda value: value[0], reverse=True)
        if scored and scored[0][0] >= 0.9 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
            return scored[0][1], scored[0][0], None
        if scored and scored[0][0] >= 0.65:
            return None, scored[0][0], scored[0][1]
        return None, 0.0, None

    def split_partition(self, partition_id: str, scopes: list[DiscoveryScope]) -> None:
        with Session(self.engine) as session, session.begin():
            partition = session.get(CrawlPartitionRow, partition_id)
            if partition is None:
                raise ValueError("Unknown partition")
            partition.status = PartitionStatus.SPLIT
            for scope in scopes:
                exists = session.scalar(
                    select(CrawlPartitionRow.id).where(
                        CrawlPartitionRow.job_id == partition.job_id,
                        CrawlPartitionRow.key == scope.key,
                    )
                )
                if exists is None:
                    session.add(self._partition_row(partition.job_id, scope))

    def fail_partition(self, partition_id: str, code: str, *, blocked: bool = False) -> None:
        with Session(self.engine) as session, session.begin():
            partition = session.get(CrawlPartitionRow, partition_id)
            if partition:
                partition.status = PartitionStatus.BLOCKED if blocked else PartitionStatus.FAILED
                partition.error_code = code[:64]
                partition.updated_at = utc_now()

    def finish_job(self, job_id: str) -> CrawlSummary:
        with Session(self.engine) as session, session.begin():
            job = session.get(CrawlJobRow, job_id)
            if job is None:
                raise ValueError("Unknown job")
            partitions = session.scalars(
                select(CrawlPartitionRow).where(CrawlPartitionRow.job_id == job_id)
            ).all()
            failed = sum(
                row.status in {PartitionStatus.FAILED, PartitionStatus.BLOCKED, PartitionStatus.TRUNCATED}
                for row in partitions
            )
            active = sum(row.status in {PartitionStatus.QUEUED, PartitionStatus.RUNNING} for row in partitions)
            if active:
                job.status = JobStatus.RUNNING
            else:
                job.status = JobStatus.PARTIAL if failed else JobStatus.COMPLETED
                job.completed_at = utc_now()
                job.lease_owner = None
                job.lease_expires_at = None
            job.updated_at = utc_now()
            city = session.get(CityRow, job.city_id)
            category = session.get(CategoryRow, job.category_id)
            unique_locations = int(
                session.scalar(
                    select(func.count(func.distinct(SourceCardRow.location_id)))
                    .select_from(DiscoveryEvidenceRow)
                    .join(SourceCardRow, SourceCardRow.id == DiscoveryEvidenceRow.source_card_id)
                    .where(DiscoveryEvidenceRow.job_id == job_id)
                )
                or 0
            )
            return CrawlSummary(
                job_id=job.id,
                city=city.name if city else "unknown",
                category=category.display_name if category else "unknown",
                status=JobStatus(job.status),
                discovered=job.discovered_count,
                unique_locations=unique_locations,
                partitions_completed=sum(
                    row.status in {PartitionStatus.COMPLETED, PartitionStatus.EMPTY}
                    for row in partitions
                ),
                partitions_failed=failed,
                coverage_uncertain=bool(failed),
            )

    def list_locations(
        self,
        *,
        city_name: str | None = None,
        query: str | None = None,
        category_id: str | None = None,
        category_query: str | None = None,
        zone_type: str | None = None,
        zone_name: str | None = None,
        center_latitude: float | None = None,
        center_longitude: float | None = None,
        radius_km: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        radius_requested = any(
            value is not None
            for value in (center_latitude, center_longitude, radius_km)
        )
        if radius_requested and (
            center_latitude is None
            or center_longitude is None
            or radius_km not in {1, 5, 10}
        ):
            raise ValueError("Radius search requires coordinates and radius 1, 5, or 10 km")
        with Session(self.engine) as session:
            statement = (
                select(LocationRow, CityRow.name)
                .join(CityRow, CityRow.id == LocationRow.city_id)
                .where(LocationRow.status == "active")
            )
            if city_name:
                statement = statement.where(CityRow.normalized_name == normalize_text(city_name))
            if query:
                pattern = f"%{normalize_text(query)}%"
                statement = statement.where(
                    or_(
                        LocationRow.normalized_name.like(pattern),
                        LocationRow.normalized_address.like(pattern),
                    )
                )
            if category_id or category_query:
                statement = statement.join(
                    LocationCategoryRow,
                    LocationCategoryRow.location_id == LocationRow.id,
                ).join(
                    CategoryRow,
                    CategoryRow.id == LocationCategoryRow.category_id,
                ).where(LocationCategoryRow.active.is_(True))
                if category_id:
                    statement = statement.where(CategoryRow.id == category_id)
                if category_query:
                    statement = statement.where(
                        CategoryRow.key.like(f"%{normalize_text(category_query)}%")
                    )
                statement = statement.where(
                    LocationCategoryRow.active.is_(True),
                    CategoryRow.active.is_(True),
                )
            if zone_type and zone_name and zone_type in {"district", "metro"}:
                statement = statement.join(
                    LocationAreaRow,
                    LocationAreaRow.location_id == LocationRow.id,
                ).join(AreaRow, AreaRow.id == LocationAreaRow.area_id).where(
                    LocationAreaRow.active.is_(True),
                    AreaRow.active.is_(True),
                    AreaRow.kind == zone_type,
                    AreaRow.normalized_name == normalize_text(zone_name),
                )
            if radius_requested:
                latitude_delta = float(radius_km) / 111.0
                longitude_scale = max(
                    0.1,
                    math.cos(math.radians(float(center_latitude))),
                )
                longitude_delta = float(radius_km) / (111.0 * longitude_scale)
                statement = statement.where(
                    LocationRow.latitude.between(
                        float(center_latitude) - latitude_delta,
                        float(center_latitude) + latitude_delta,
                    ),
                    LocationRow.longitude.between(
                        float(center_longitude) - longitude_delta,
                        float(center_longitude) + longitude_delta,
                    ),
                )
                rows = session.execute(statement.distinct()).all()
                nearby = []
                for row, city in rows:
                    distance = haversine_km(
                        center_latitude,
                        center_longitude,
                        row.latitude,
                        row.longitude,
                    )
                    if distance <= float(radius_km):
                        item = self._location_summary(session, row, city)
                        item["distance_km"] = round(distance, 3)
                        nearby.append(item)
                nearby.sort(
                    key=lambda item: (
                        item["distance_km"],
                        normalize_text(item["name"]),
                        item["id"],
                    )
                )
                return nearby[offset : offset + min(limit, 100)]
            rows = session.execute(
                statement.distinct()
                .order_by(LocationRow.canonical_name)
                .offset(offset)
                .limit(min(limit, 100))
            ).all()
            return [self._location_summary(session, row, city) for row, city in rows]

    def list_categories(
        self,
        city_name: str,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = (
                select(
                    CategoryRow.id,
                    CategoryRow.display_name,
                    func.count(func.distinct(LocationRow.id)),
                )
                .join(LocationCategoryRow, LocationCategoryRow.category_id == CategoryRow.id)
                .join(LocationRow, LocationRow.id == LocationCategoryRow.location_id)
                .join(CityRow, CityRow.id == LocationRow.city_id)
                .where(
                    CityRow.normalized_name == normalize_text(city_name),
                    LocationRow.status == "active",
                    LocationCategoryRow.active.is_(True),
                )
            )
            if query:
                statement = statement.where(
                    CategoryRow.key.like(f"%{normalize_text(query)}%")
                )
            rows = session.execute(
                statement.group_by(CategoryRow.id, CategoryRow.display_name)
                .order_by(func.count(func.distinct(LocationRow.id)).desc(), CategoryRow.display_name)
                .limit(min(limit, 30))
            ).all()
            return [
                {"id": category_id, "name": name, "count": int(count)}
                for category_id, name, count in rows
            ]

    def get_category(self, category_id: str) -> dict[str, str] | None:
        with Session(self.engine) as session:
            row = session.get(CategoryRow, category_id)
            if row is None or not row.active:
                return None
            return {"id": row.id, "name": row.display_name}

    def list_zones(
        self,
        city_name: str,
        zone_type: str,
        *,
        category_id: str | None = None,
        category_query: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        if zone_type not in {"district", "metro"}:
            return []
        with Session(self.engine) as session:
            statement = (
                select(AreaRow.name, func.count(func.distinct(LocationRow.id)))
                .join(LocationAreaRow, LocationAreaRow.area_id == AreaRow.id)
                .join(LocationRow, LocationRow.id == LocationAreaRow.location_id)
                .join(CityRow, CityRow.id == LocationRow.city_id)
                .where(
                    CityRow.normalized_name == normalize_text(city_name),
                    LocationRow.status == "active",
                    AreaRow.kind == zone_type,
                    AreaRow.active.is_(True),
                    LocationAreaRow.active.is_(True),
                )
            )
            if category_id or category_query:
                statement = statement.join(
                    LocationCategoryRow,
                    LocationCategoryRow.location_id == LocationRow.id,
                ).join(CategoryRow, CategoryRow.id == LocationCategoryRow.category_id)
                if category_id:
                    statement = statement.where(CategoryRow.id == category_id)
                if category_query:
                    statement = statement.where(
                        CategoryRow.key.like(f"%{normalize_text(category_query)}%")
                    )
            rows = session.execute(
                statement.group_by(AreaRow.id, AreaRow.name)
                .order_by(func.count(func.distinct(LocationRow.id)).desc(), AreaRow.name)
                .limit(min(limit, 50))
            ).all()
            return [{"name": name, "count": int(count)} for name, count in rows]

    def get_location(self, location_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.get(LocationRow, location_id)
            if row is None or row.status != "active":
                return None
            city = session.get(CityRow, row.city_id)
            result = self._location_summary(session, row, city.name if city else "unknown")
            result["profile"] = row.profile_json
            result["sources"] = [
                {
                    "provider": source.provider,
                    "provider_id": source.provider_object_id,
                    "url": source.source_url,
                    "rating": source.rating,
                    "reviews_count": source.reviews_count,
                }
                for source in session.scalars(
                    select(SourceCardRow).where(SourceCardRow.location_id == row.id)
                ).all()
            ]
            return result

    def coordinate_locations(self, city_name: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    LocationRow.id,
                    LocationRow.longitude,
                    LocationRow.latitude,
                )
                .join(CityRow, CityRow.id == LocationRow.city_id)
                .where(
                    CityRow.normalized_name == normalize_text(city_name),
                    LocationRow.status == "active",
                    LocationRow.longitude.is_not(None),
                    LocationRow.latitude.is_not(None),
                )
            ).all()
            return [
                {"id": location_id, "longitude": longitude, "latitude": latitude}
                for location_id, longitude, latitude in rows
            ]

    def replace_boundary_districts(
        self,
        city_name: str,
        assignments: dict[str, str],
    ) -> int:
        updated = 0
        with Session(self.engine) as session, session.begin():
            locations = session.scalars(
                select(LocationRow)
                .join(CityRow, CityRow.id == LocationRow.city_id)
                .where(
                    CityRow.normalized_name == normalize_text(city_name),
                    LocationRow.status == "active",
                )
            ).all()
            for location in locations:
                district = assignments.get(location.id)
                self._replace_areas(
                    session,
                    location,
                    [("district", district)] if district else [],
                    source="openstreetmap_boundary",
                )
                if district:
                    updated += 1
        return updated

    @staticmethod
    def _location_summary(session: Session, row: LocationRow, city: str) -> dict[str, Any]:
        categories = session.scalars(
            select(CategoryRow.display_name)
            .join(LocationCategoryRow, LocationCategoryRow.category_id == CategoryRow.id)
            .where(LocationCategoryRow.location_id == row.id, LocationCategoryRow.active.is_(True))
        ).all()
        areas = session.execute(
            select(AreaRow.kind, AreaRow.name)
            .join(LocationAreaRow, LocationAreaRow.area_id == AreaRow.id)
            .where(
                LocationAreaRow.location_id == row.id,
                LocationAreaRow.active.is_(True),
                AreaRow.active.is_(True),
            )
            .order_by(LocationAreaRow.priority, AreaRow.name)
        ).all()
        districts = [name for kind, name in areas if kind == "district"]
        metros = [name for kind, name in areas if kind == "metro"]
        primary_source = session.scalar(
            select(SourceCardRow)
            .where(
                SourceCardRow.location_id == row.id,
                SourceCardRow.active.is_(True),
            )
            .order_by(
                (SourceCardRow.provider == "yandex_maps").desc(),
                SourceCardRow.last_seen_at.desc(),
            )
        )
        profile = row.profile_json or {}
        return {
            "id": row.id,
            "name": row.canonical_name,
            "address": row.canonical_address,
            "city": city,
            "longitude": row.longitude,
            "latitude": row.latitude,
            "district": districts[0] if districts else None,
            "metro": metros[0] if metros else None,
            "districts": districts,
            "metros": metros,
            "categories": list(categories),
            "rating": profile.get("rating") or (
                primary_source.rating if primary_source else None
            ),
            "reviews_count": profile.get("reviews_count") or (
                primary_source.reviews_count if primary_source else None
            ),
            "has_passport": row.profile_json is not None,
            "completeness": row.completeness,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def pending_yandex_cards(
        self,
        limit: int = 10,
        refresh_hours: int = 168,
    ) -> list[dict[str, str]]:
        stale_before = utc_now() - timedelta(hours=max(1, refresh_hours))
        with Session(self.engine) as session:
            rows = session.execute(
                select(SourceCardRow, LocationRow)
                .join(LocationRow, LocationRow.id == SourceCardRow.location_id)
                .where(
                    SourceCardRow.provider == "yandex_maps",
                    or_(
                        LocationRow.profile_json.is_(None),
                        LocationRow.profile_checked_at.is_(None),
                        LocationRow.profile_checked_at < stale_before,
                    ),
                    or_(
                        SourceCardRow.detail_retry_at.is_(None),
                        SourceCardRow.detail_retry_at <= utc_now(),
                    ),
                    LocationRow.status == "active",
                )
                .order_by(
                    (SourceCardRow.detail_failures == 0).desc(),
                    SourceCardRow.first_seen_at,
                )
                .limit(limit)
            ).all()
            return [
                {
                    "location_id": location.id,
                    "provider_id": source.provider_object_id,
                    "source_url": source.source_url or "",
                }
                for source, location in rows
            ]

    def record_detail_failure(self, provider_id: str, code: str) -> None:
        with Session(self.engine) as session, session.begin():
            source = session.scalar(
                select(SourceCardRow).where(
                    SourceCardRow.provider == "yandex_maps",
                    SourceCardRow.provider_object_id == provider_id,
                )
            )
            if source is None:
                return
            source.detail_failures += 1
            delay_minutes = min(24 * 60, 15 * (2 ** min(source.detail_failures - 1, 7)))
            source.detail_retry_at = utc_now() + timedelta(minutes=delay_minutes)
            source.detail_error_code = code[:64]

    def backfill_profile_areas(self, limit: int = 1000) -> int:
        updated = 0
        with Session(self.engine) as session, session.begin():
            locations = session.scalars(
                select(LocationRow)
                .where(
                    LocationRow.profile_json.is_not(None),
                    LocationRow.area_hash.is_(None),
                )
                .order_by(LocationRow.updated_at)
                .limit(max(1, min(limit, 5000)))
            ).all()
            for location in locations:
                try:
                    profile = SalonProfile.model_validate(location.profile_json)
                except (TypeError, ValueError):
                    continue
                self._replace_profile_areas(session, location, profile)
                updated += 1
        return updated

    def reconcile_completed_jobs(self, job_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(job_ids))
        if not ids:
            return
        with Session(self.engine) as session, session.begin():
            jobs = session.scalars(
                select(CrawlJobRow).where(CrawlJobRow.id.in_(ids))
            ).all()
            if not jobs or any(job.status != JobStatus.COMPLETED for job in jobs):
                return
            city_id = jobs[0].city_id
            provider = jobs[0].provider
            if any(job.city_id != city_id or job.provider != provider for job in jobs):
                raise ValueError("Reconciliation jobs must share city and provider")
            seen_source_ids = set(
                session.scalars(
                    select(DiscoveryEvidenceRow.source_card_id).where(
                        DiscoveryEvidenceRow.job_id.in_(ids)
                    )
                ).all()
            )
            provider_sources = session.execute(
                select(SourceCardRow, LocationRow)
                .join(LocationRow, LocationRow.id == SourceCardRow.location_id)
                .where(
                    SourceCardRow.provider == provider,
                    LocationRow.city_id == city_id,
                )
            ).all()
            for source, _location in provider_sources:
                source.active = source.id in seen_source_ids
            for job in jobs:
                seen_locations = set(
                    session.scalars(
                        select(SourceCardRow.location_id)
                        .join(
                            DiscoveryEvidenceRow,
                            DiscoveryEvidenceRow.source_card_id == SourceCardRow.id,
                        )
                        .where(DiscoveryEvidenceRow.job_id == job.id)
                    ).all()
                )
                memberships = session.scalars(
                    select(LocationCategoryRow)
                    .join(LocationRow, LocationRow.id == LocationCategoryRow.location_id)
                    .where(
                        LocationRow.city_id == city_id,
                        LocationCategoryRow.category_id == job.category_id,
                    )
                ).all()
                for membership in memberships:
                    if membership.location_id in seen_locations:
                        membership.active = True
            location_ids = {location.id for _source, location in provider_sources}
            for location_id in location_ids:
                has_active_source = session.scalar(
                    select(SourceCardRow.id).where(
                        SourceCardRow.location_id == location_id,
                        SourceCardRow.active.is_(True),
                    ).limit(1)
                )
                location = session.get(LocationRow, location_id)
                if location is not None:
                    location.status = "active" if has_active_source else "inactive"

    def save_profile(self, location_id: str, profile: SalonProfile) -> bool:
        payload = profile.model_dump(mode="json")
        content_hash = stable_hash(
            profile.model_dump(mode="json", exclude={"collected_at"})
        )
        with Session(self.engine) as session, session.begin():
            location = session.get(LocationRow, location_id)
            if location is None:
                raise ValueError("Unknown location")
            if location.profile_hash == content_hash:
                location.profile_checked_at = utc_now()
                location.updated_at = utc_now()
                session.execute(
                    SourceCardRow.__table__.update()
                    .where(
                        SourceCardRow.location_id == location_id,
                        SourceCardRow.provider == "yandex_maps",
                    )
                    .values(detail_failures=0, detail_retry_at=None, detail_error_code=None)
                )
                return False
            location.profile_json = payload
            location.profile_hash = content_hash
            location.profile_checked_at = utc_now()
            location.completeness = profile_completeness(profile)
            self._replace_profile_areas(session, location, profile)
            location.updated_at = utc_now()
            snapshot = session.scalar(
                select(PassportSnapshotRow).where(
                    PassportSnapshotRow.location_id == location_id,
                    PassportSnapshotRow.schema_version == 1,
                    PassportSnapshotRow.content_hash == content_hash,
                )
            )
            if snapshot is None:
                session.add(
                    PassportSnapshotRow(
                        id=str(uuid.uuid4()),
                        location_id=location_id,
                        schema_version=1,
                        content_hash=content_hash,
                        content_json=payload,
                    )
                )
                session.flush()
            old_ids = session.scalars(
                select(PassportSnapshotRow.id)
                .where(PassportSnapshotRow.location_id == location_id)
                .order_by(PassportSnapshotRow.collected_at.desc())
                .offset(self.snapshot_limit)
            ).all()
            if old_ids:
                session.execute(
                    delete(PassportSnapshotRow).where(PassportSnapshotRow.id.in_(old_ids))
                )
            session.execute(
                SourceCardRow.__table__.update()
                .where(
                    SourceCardRow.location_id == location_id,
                    SourceCardRow.provider == "yandex_maps",
                )
                .values(detail_failures=0, detail_retry_at=None, detail_error_code=None)
            )
            return True

    @staticmethod
    def _replace_profile_areas(
        session: Session,
        location: LocationRow,
        profile: SalonProfile,
    ) -> None:
        desired: list[tuple[str, str]] = []
        if profile.district:
            desired.append(("district", profile.district))
        desired.extend(("metro", station) for station in profile.metro_stations)
        desired = list(dict.fromkeys(desired))
        area_hash = stable_hash(
            [(kind, normalize_text(name)) for kind, name in desired]
        )
        if location.area_hash == area_hash:
            return
        CatalogRepository._replace_areas(
            session,
            location,
            desired,
            source="yandex_profile",
        )
        location.area_hash = area_hash

    @staticmethod
    def _replace_discovery_areas(
        session: Session,
        location: LocationRow,
        card: DiscoveryCard,
    ) -> None:
        desired: list[tuple[str, str]] = []
        if card.district:
            desired.append(("district", card.district))
        desired.extend(("metro", station) for station in card.metro_stations)
        desired = list(dict.fromkeys(desired))
        if not desired:
            return
        CatalogRepository._replace_areas(
            session,
            location,
            desired,
            source=f"{card.provider}_discovery",
        )

    @staticmethod
    def _replace_areas(
        session: Session,
        location: LocationRow,
        desired: list[tuple[str, str]],
        *,
        source: str,
    ) -> None:
        source_priority = _area_source_priority(source)
        existing = session.scalars(
            select(LocationAreaRow).where(
                LocationAreaRow.location_id == location.id,
                LocationAreaRow.source == source,
            )
        ).all()
        for membership in existing:
            membership.active = False
        for priority, (kind, name) in enumerate(desired):
            normalized = normalize_text(name)
            kind_memberships = session.scalars(
                select(LocationAreaRow)
                .join(AreaRow, AreaRow.id == LocationAreaRow.area_id)
                .where(
                    LocationAreaRow.location_id == location.id,
                    LocationAreaRow.active.is_(True),
                    AreaRow.kind == kind,
                )
            ).all()
            if any(
                _area_source_priority(membership.source) > source_priority
                for membership in kind_memberships
            ):
                continue
            for lower in kind_memberships:
                if _area_source_priority(lower.source) < source_priority:
                    lower.active = False
            area = session.scalar(
                select(AreaRow).where(
                    AreaRow.city_id == location.city_id,
                    AreaRow.kind == kind,
                    AreaRow.normalized_name == normalized,
                )
            )
            if area is None:
                area = AreaRow(
                    id=str(uuid.uuid4()),
                    city_id=location.city_id,
                    kind=kind,
                    name=name,
                    normalized_name=normalized,
                    source=source,
                )
                session.add(area)
                session.flush()
            else:
                area.name = name
                area.active = True
                area.updated_at = utc_now()
            membership = session.get(
                LocationAreaRow,
                {"location_id": location.id, "area_id": area.id},
            )
            if membership is None:
                session.add(
                    LocationAreaRow(
                        location_id=location.id,
                        area_id=area.id,
                        source=source,
                        priority=priority,
                    )
                )
            else:
                if (
                    membership.active
                    and _area_source_priority(membership.source) > source_priority
                ):
                    continue
                membership.source = source
                membership.active = True
                membership.confidence = 1.0
                membership.priority = priority

    def location_features(self, city_name: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(LocationRow)
                .join(CityRow, CityRow.id == LocationRow.city_id)
                .where(CityRow.normalized_name == normalize_text(city_name), LocationRow.status == "active")
            ).all()
            result = []
            for row in rows:
                categories = set(
                    session.scalars(
                        select(CategoryRow.key)
                        .join(LocationCategoryRow, LocationCategoryRow.category_id == CategoryRow.id)
                        .where(
                            LocationCategoryRow.location_id == row.id,
                            LocationCategoryRow.active.is_(True),
                            CategoryRow.active.is_(True),
                        )
                    ).all()
                )
                profile = row.profile_json or {}
                primary_source = session.scalar(
                    select(SourceCardRow)
                    .where(
                        SourceCardRow.location_id == row.id,
                        SourceCardRow.active.is_(True),
                    )
                    .order_by(
                        (SourceCardRow.provider == "yandex_maps").desc(),
                        SourceCardRow.last_seen_at.desc(),
                    )
                )
                service_names = {
                    normalize_text(str(item.get("name") or ""))
                    for item in profile.get("services") or []
                    if item.get("name")
                }
                areas = session.execute(
                    select(AreaRow.kind, AreaRow.name)
                    .join(LocationAreaRow, LocationAreaRow.area_id == AreaRow.id)
                    .where(
                        LocationAreaRow.location_id == row.id,
                        LocationAreaRow.active.is_(True),
                        AreaRow.active.is_(True),
                    )
                    .order_by(LocationAreaRow.priority, AreaRow.name)
                ).all()
                result.append(
                    {
                        "id": row.id,
                        "name": row.canonical_name,
                        "address": row.canonical_address,
                        "latitude": row.latitude,
                        "longitude": row.longitude,
                        "categories": categories,
                        "services": service_names,
                        "reviews_summary": profile.get("reviews_summary"),
                        "review_texts": [
                            str(review.get("text") or "")
                            for review in profile.get("reviews") or []
                            if isinstance(review, dict) and review.get("text")
                        ],
                        "masters": list(profile.get("masters") or ()),
                        "awards": list(profile.get("awards") or ()),
                        "rating": profile.get("rating") or (
                            primary_source.rating if primary_source else None
                        ),
                        "reviews_count": profile.get("reviews_count") or (
                            primary_source.reviews_count if primary_source else None
                        ),
                        "district": next(
                            (
                                name
                                for kind, name in areas
                                if kind == "district"
                            ),
                            None,
                        ),
                        "metros": [
                            name
                            for kind, name in areas
                            if kind == "metro"
                        ],
                    }
                )
            return result

    def replace_competitors(
        self,
        matches: list[CompetitorMatch],
        location_ids: Iterable[str] | None = None,
    ) -> None:
        scoped_ids = set(location_ids or ()) | {match.location_id for match in matches}
        if not scoped_ids:
            return
        with Session(self.engine) as session, session.begin():
            session.execute(delete(CompetitorRow).where(CompetitorRow.location_id.in_(scoped_ids)))
            session.add_all(
                CompetitorRow(
                    location_id=match.location_id,
                    competitor_id=match.competitor_id,
                    score=match.score,
                    distance_km=match.distance_km,
                    reasons_json=match.reasons,
                )
                for match in matches
            )

    def competitors(self, location_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(CompetitorRow, LocationRow.canonical_name)
                .join(LocationRow, LocationRow.id == CompetitorRow.competitor_id)
                .where(CompetitorRow.location_id == location_id)
                .order_by(CompetitorRow.score.desc())
                .limit(limit)
            ).all()
            return [
                {
                    "location_id": row.competitor_id,
                    "name": name,
                    "score": row.score,
                    "distance_km": row.distance_km,
                    "reasons": row.reasons_json,
                }
                for row, name in rows
            ]

    def status(self, city_name: str | None = None) -> dict[str, Any]:
        with Session(self.engine) as session:
            location_query = select(func.count(LocationRow.id)).where(
                LocationRow.status == "active"
            )
            job_query = select(CrawlJobRow.status, func.count(CrawlJobRow.id)).group_by(CrawlJobRow.status)
            if city_name:
                normalized = normalize_text(city_name)
                location_query = location_query.join(CityRow).where(CityRow.normalized_name == normalized)
                job_query = job_query.join(CityRow).where(CityRow.normalized_name == normalized)
            locations = int(session.scalar(location_query) or 0)
            jobs = {status: count for status, count in session.execute(job_query).all()}
            profile_query = select(func.count(LocationRow.id)).where(
                LocationRow.profile_json.is_not(None),
                LocationRow.status == "active",
            )
            if city_name:
                profile_query = profile_query.join(CityRow).where(
                    CityRow.normalized_name == normalize_text(city_name)
                )
            profiles = int(session.scalar(profile_query) or 0)
            return {"locations": locations, "profiles": profiles, "jobs": jobs}

    def cleanup(self, completed_job_days: int = 30) -> dict[str, int]:
        cutoff = utc_now() - timedelta(days=max(1, completed_job_days))
        with Session(self.engine) as session, session.begin():
            old_jobs = session.scalars(
                select(CrawlJobRow.id).where(
                    CrawlJobRow.status.in_([JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.CANCELLED]),
                    CrawlJobRow.completed_at < cutoff,
                ).limit(500)
            ).all()
            if old_jobs:
                partition_ids = session.scalars(
                    select(CrawlPartitionRow.id).where(CrawlPartitionRow.job_id.in_(old_jobs))
                ).all()
                session.execute(delete(DiscoveryEvidenceRow).where(DiscoveryEvidenceRow.job_id.in_(old_jobs)))
                if partition_ids:
                    session.execute(delete(CrawlPartitionRow).where(CrawlPartitionRow.id.in_(partition_ids)))
                session.execute(delete(CrawlJobRow).where(CrawlJobRow.id.in_(old_jobs)))
            return {"jobs_removed": len(old_jobs)}


def normalize_text(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def addresses_equivalent(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= 8 and longer.endswith(shorter)


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalized_phones(values: Iterable[str]) -> list[str]:
    result = []
    for value in values:
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 10:
            result.append(digits[-10:])
    return list(dict.fromkeys(result))


def haversine_km(
    latitude_a: float | None,
    longitude_a: float | None,
    latitude_b: float | None,
    longitude_b: float | None,
) -> float:
    if None in {latitude_a, longitude_a, latitude_b, longitude_b}:
        return math.inf
    radius = 6371.0088
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _area_source_priority(source: str) -> int:
    if source == "yandex_profile":
        return 40
    if source == "yandex_maps_discovery":
        return 30
    if source.endswith("_discovery"):
        return 20
    if source == "openstreetmap_boundary":
        return 10
    return 0


def profile_completeness(profile: SalonProfile) -> float:
    checks = (
        bool(profile.name),
        bool(profile.address),
        profile.rating is not None,
        bool(profile.reviews),
        bool(profile.services),
        bool(profile.opening_hours),
        bool(profile.categories),
        bool(profile.sources),
    )
    return round(sum(checks) / len(checks), 3)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
