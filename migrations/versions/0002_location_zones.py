"""Persist reusable district and metro zones with location membership."""

import sqlalchemy as sa
from alembic import op


revision = "0002_location_zones"
down_revision = "0001_catalog_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_locations", sa.Column("area_hash", sa.String(64)))
    op.create_index("ix_catalog_locations_area_hash", "catalog_locations", ["area_hash"])
    op.create_table(
        "catalog_areas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("city_id", sa.String(36), sa.ForeignKey("catalog_cities.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("city_id", "kind", "normalized_name"),
    )
    op.create_index("ix_catalog_areas_city_id", "catalog_areas", ["city_id"])
    op.create_index("ix_catalog_areas_kind", "catalog_areas", ["kind"])
    op.create_index("ix_catalog_areas_normalized_name", "catalog_areas", ["normalized_name"])
    op.create_table(
        "catalog_location_areas",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("catalog_locations.id"), primary_key=True),
        sa.Column("area_id", sa.String(36), sa.ForeignKey("catalog_areas.id"), primary_key=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("catalog_location_areas")
    op.drop_index("ix_catalog_areas_normalized_name", table_name="catalog_areas")
    op.drop_index("ix_catalog_areas_kind", table_name="catalog_areas")
    op.drop_index("ix_catalog_areas_city_id", table_name="catalog_areas")
    op.drop_table("catalog_areas")
    op.drop_index("ix_catalog_locations_area_hash", table_name="catalog_locations")
    op.drop_column("catalog_locations", "area_hash")
