from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.models import SalonProfile


class SourceArchive:
    """Append-only, source-native snapshots kept outside the canonical catalogue."""

    def __init__(self, directory: str | Path = "data/source_archives") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        for provider in ("yandex", "twogis"):
            self._ensure_schema(self.directory / f"{provider}_raw.db")

    def save(self, profile: SalonProfile) -> bool:
        provider = "twogis" if profile.primary_provider == "2gis" else "yandex"
        path = self.directory / f"{provider}_raw.db"
        payload = profile.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        with sqlite3.connect(path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO source_profiles VALUES (?, ?, ?, ?)",
                (
                    profile.provider_id,
                    datetime.now(UTC).isoformat(),
                    digest,
                    encoded.decode("utf-8"),
                ),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _ensure_schema(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS source_profiles (
                provider_id TEXT NOT NULL, collected_at TEXT NOT NULL,
                content_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY (provider_id, content_hash))"""
            )
