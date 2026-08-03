# Architecture memory

- Yandex Maps is the primary identity/card source. Additional map providers
  enrich reviews but must not overwrite a valid primary card.
- Public map data and online-booking data are separate contours. YCLIENTS code
  remains available but must not silently inject historic staff into map reports.
- `app/agents/` contains runtime report workflow roles. `.codex/agents/` contains
  development roles and is not imported by the application.
- Telegram is the current UI; `POST /reports` remains the product-facing API
  boundary for future frontends.
- `app/catalog/` is the persistent pre-SINDY city catalogue. Source cards keep
  provider identity and provenance separately from canonical locations.
- Discovery jobs and geographic partitions are durable and idempotent. Detail
  enrichment is bounded; passport snapshots are content-addressed and retained
  only when facts change.
- SQLite is the local QA fallback. PostgreSQL/PostGIS is the production target;
  SINDY delivery remains disabled until the staging contract and acceptance gate
  are complete.
- Competitor scores are deterministic and token-free. LLM review analysis is
  allowed only after the underlying review content hash changes.
- Telegram catalogue browsing is DB-only. Districts and metro stations are
  normalized catalogue areas linked many-to-many with canonical locations;
  transient Telegram navigation state is never stored in the database.
- District memberships may come from map discovery/profile data or a cached
  OpenStreetMap boundary assignment. Radius browsing uses ephemeral Telegram
  coordinates and performs only local catalogue queries.
- Comparison narratives are generated from saved evidence. Missing ratings,
  reviews or services remain explicitly unknown and are never converted to
  zero-valued weaknesses or strengths.
