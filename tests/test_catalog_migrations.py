from alembic import command
from alembic.autogenerate import produce_migrations
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.catalog.db import Base


def test_catalog_migration_up_and_down_is_reproducible(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("CATALOG_DATABASE_URL", database_url)
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "profile_checked_at" in {
        column["name"] for column in inspector.get_columns("catalog_locations")
    }
    assert {"detail_failures", "detail_retry_at", "detail_error_code"}.issubset(
        {
            column["name"]
            for column in inspector.get_columns("catalog_source_cards")
        }
    )
    with engine.connect() as connection:
        drift = produce_migrations(
            MigrationContext.configure(connection),
            Base.metadata,
        )
    assert drift.upgrade_ops.is_empty()

    command.downgrade(config, "base")
    remaining = set(inspect(create_engine(database_url)).get_table_names())
    assert not {name for name in remaining if name.startswith("catalog_")}
