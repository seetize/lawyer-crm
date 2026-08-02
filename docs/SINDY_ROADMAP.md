# SINDY City Parser — implementation roadmap

The order below is binding unless a later product decision is recorded. Avoid
starting competitor AI or SINDY integration before catalogue identity and data
quality are reliable.

## Phase 0 — contracts and fixtures (implemented)

- Freeze pilot city, categories and map search scopes.
- Capture sanitized Yandex and 2GIS fixtures for discovery pages.
- Define canonical IDs, source IDs and job state transitions.
- Add configuration for city bounds, category queries and crawl limits.

Exit: deterministic discovery parsers and fixtures cover pagination, empty
pages, advertising results and overlapping categories.

## Phase 1 — database foundation (implemented; PostGIS runtime validation pending)

- Add PostgreSQL/PostGIS locally with migrations.
- Model cities, areas, locations, categories, source cards, collection jobs,
  snapshots and merge decisions.
- Build idempotent upserts and repository interfaces.
- Preserve source provenance and collection timestamps.

Exit: the same source result can be written repeatedly without duplicates or
lost category membership.

## Phase 2 — Yaroslavl discovery crawler (implemented; full pilot crawl pending)

- Enumerate every visible result for the three pilot categories.
- Traverse pagination and geographic partitions with checkpoints.
- Exclude ads from organic rank while retaining explicit source evidence when
  useful.
- Resume after interruption from the last durable checkpoint.
- Store lightweight cards first; enqueue detail collection separately.

Exit: Telegram shows crawl progress and a unique city catalogue with category
counts and source coverage.

## Phase 3 — identity resolution (deterministic core implemented; QA decisions pending)

- Normalize names, phones, domains, addresses and coordinates.
- Implement deterministic strong matches and scored weak candidates.
- Add reversible merges and a Telegram QA flow for ambiguous duplicates.
- Run cross-category and Yandex/2GIS reconciliation.

Exit: a multi-category salon is one canonical location; distinct branches are
not merged.

## Phase 4 — passport enrichment (versioned bounded worker implemented)

- Convert the existing single-place provider workflow into queued detail jobs.
- Store reviews/replies, services, prices, hours, map masters, news, awards and
  search rankings as versioned facts.
- Add incremental refresh and change detection.
- Track missing public data separately from collection failure.

Exit: any catalogue location opens as a complete, sourced digital passport in
Telegram without a new synchronous 90-second scrape.

## Phase 5 — competitor engine (deterministic MVP implemented)

- Define category/service similarity, geographic scope and price segments.
- Build spatial candidate selection with PostGIS.
- Calculate versioned competitor scores and explanation factors.
- Produce pairwise comparisons and city/category benchmarks.

Exit: Telegram returns competitors, evidence and relative strengths for every
eligible pilot salon.

## Phase 6 — SINDY handoff

- Agree a versioned passport and competitor payload with the SINDY core.
- Add authenticated idempotent upsert and analysis-job integration.
- Include provenance, freshness and warnings in every payload.
- Add contract tests and a staging-only end-to-end smoke test.

Exit: a validated Telegram passport produces the same structured facts inside
SINDY, and repeated delivery causes no duplicates.

## Phase 7 — scale beyond the pilot (scheduler/watchdog foundation implemented)

- Add scheduled incremental refresh, queue prioritization and source budgets.
- Partition large cities by districts and metro/transport clusters.
- Add monitoring for coverage drift, blocks, stale data and restart storms.
- Load-test the database and workers before Moscow/Saint Petersburg collection.

Exit: a new city is configuration and taxonomy work, not a new parser.

## Next action when work resumes

1. Run the complete three-category Yaroslavl crawl without smoke-test caps.
2. Complete the manual identity and passport samples in the acceptance plan.
3. Validate migrations, locking and spatial queries on PostgreSQL/PostGIS.
4. Add Telegram actions for ambiguous merge decisions and pairwise benchmarks.
5. Obtain the versioned SINDY staging contract before implementing Phase 6.

Resume instruction for a new Codex session:

> Read `docs/SINDY_CITY_PARSER.md`, `docs/SINDY_ROADMAP.md` and
> `docs/SINDY_ACCEPTANCE.md`; inspect the current Git state; continue from
> “Next action when work resumes” without re-planning completed phases.
