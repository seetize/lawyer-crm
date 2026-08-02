"""Create the frozen SINDY catalogue v1 schema."""

import sqlalchemy as sa
from alembic import op


revision = "0001_catalog_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(160), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "catalog_cities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.String(160), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("west", sa.Float(), nullable=False),
        sa.Column("south", sa.Float(), nullable=False),
        sa.Column("east", sa.Float(), nullable=False),
        sa.Column("north", sa.Float(), nullable=False),
        sa.Column("yandex_geo_id", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("country_code", "normalized_name"),
    )
    op.create_table(
        "catalog_crawl_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("city_id", sa.String(36), sa.ForeignKey("catalog_cities.id"), nullable=False),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("catalog_categories.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_fingerprint", sa.String(64)),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("attempt >= 0 AND attempt <= max_attempts"),
    )
    op.create_index("ix_catalog_crawl_jobs_city_id", "catalog_crawl_jobs", ["city_id"])
    op.create_index("ix_catalog_crawl_jobs_category_id", "catalog_crawl_jobs", ["category_id"])
    op.create_index("ix_catalog_crawl_jobs_status", "catalog_crawl_jobs", ["status"])
    op.create_index("ix_catalog_job_ready", "catalog_crawl_jobs", ["status", "lease_expires_at"])
    op.create_table(
        "catalog_locations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("city_id", sa.String(36), sa.ForeignKey("catalog_cities.id"), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("canonical_address", sa.String(500)),
        sa.Column("normalized_address", sa.String(500)),
        sa.Column("longitude", sa.Float()),
        sa.Column("latitude", sa.Float()),
        sa.Column("website_domain", sa.String(255)),
        sa.Column("booking_identity", sa.String(255)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("profile_json", sa.JSON()),
        sa.Column("profile_hash", sa.String(64)),
        sa.Column("profile_checked_at", sa.DateTime(timezone=True)),
        sa.Column("completeness", sa.Float(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "city_id", "normalized_name", "normalized_address", "longitude", "latitude",
        "website_domain", "booking_identity", "status", "profile_checked_at",
    ):
        op.create_index(f"ix_catalog_locations_{column}", "catalog_locations", [column])
    op.create_table(
        "catalog_competitors",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), primary_key=True),
        sa.Column("competitor_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), primary_key=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("distance_km", sa.Float()),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "catalog_crawl_partitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("catalog_crawl_jobs.id"), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("west", sa.Float(), nullable=False),
        sa.Column("south", sa.Float(), nullable=False),
        sa.Column("east", sa.Float(), nullable=False),
        sa.Column("north", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cursor_json", sa.JSON()),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("total_hint", sa.Integer()),
        sa.Column("last_raw_hash", sa.String(64)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "key"),
    )
    op.create_index("ix_catalog_crawl_partitions_job_id", "catalog_crawl_partitions", ["job_id"])
    op.create_index("ix_catalog_crawl_partitions_status", "catalog_crawl_partitions", ["status"])
    op.create_table(
        "catalog_location_categories",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), primary_key=True),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("catalog_categories.id"), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "catalog_merge_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("left_location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), nullable=False),
        sa.Column("right_location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("left_location_id <> right_location_id"),
        sa.UniqueConstraint("left_location_id", "right_location_id", "algorithm_version"),
    )
    op.create_index("ix_catalog_merge_candidates_left_location_id", "catalog_merge_candidates", ["left_location_id"])
    op.create_index("ix_catalog_merge_candidates_right_location_id", "catalog_merge_candidates", ["right_location_id"])
    op.create_table(
        "catalog_passport_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("location_id", "schema_version", "content_hash"),
    )
    op.create_index("ix_catalog_passport_snapshots_location_id", "catalog_passport_snapshots", ["location_id"])
    op.create_table(
        "catalog_source_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_object_id", sa.String(160), nullable=False),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("address", sa.String(500)),
        sa.Column("normalized_address", sa.String(500)),
        sa.Column("longitude", sa.Float()),
        sa.Column("latitude", sa.Float()),
        sa.Column("phones_json", sa.JSON(), nullable=False),
        sa.Column("website_domain", sa.String(255)),
        sa.Column("booking_identity", sa.String(255)),
        sa.Column("rating", sa.Float()),
        sa.Column("reviews_count", sa.Integer()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_failures", sa.Integer(), nullable=False),
        sa.Column("detail_retry_at", sa.DateTime(timezone=True)),
        sa.Column("detail_error_code", sa.String(64)),
        sa.UniqueConstraint("provider", "provider_object_id"),
    )
    for column in ("location_id", "normalized_name", "normalized_address", "detail_retry_at"):
        op.create_index(f"ix_catalog_source_cards_{column}", "catalog_source_cards", [column])
    op.create_table(
        "catalog_discovery_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("catalog_crawl_jobs.id"), nullable=False),
        sa.Column("partition_id", sa.String(36), sa.ForeignKey("catalog_crawl_partitions.id"), nullable=False),
        sa.Column("source_card_id", sa.String(36), sa.ForeignKey("catalog_source_cards.id"), nullable=False),
        sa.Column("category_id", sa.String(36), sa.ForeignKey("catalog_categories.id"), nullable=False),
        sa.Column("organic_position", sa.Integer()),
        sa.Column("is_advert", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "partition_id", "source_card_id", "category_id"),
    )
    for column in ("job_id", "partition_id", "source_card_id", "category_id"):
        op.create_index(f"ix_catalog_discovery_evidence_{column}", "catalog_discovery_evidence", [column])


def downgrade() -> None:
    for table in (
        "catalog_discovery_evidence",
        "catalog_source_cards",
        "catalog_passport_snapshots",
        "catalog_merge_candidates",
        "catalog_location_categories",
        "catalog_crawl_partitions",
        "catalog_competitors",
        "catalog_locations",
        "catalog_crawl_jobs",
        "catalog_cities",
        "catalog_categories",
    ):
        op.drop_table(table)
