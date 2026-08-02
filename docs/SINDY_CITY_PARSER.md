# SINDY City Parser — product specification

Status: approved working concept, implementation has not started.

Source brief: lead voice message `5438592021772149870.ogg`, received
2026-08-02. SHA-256:
`0FC79A1295CBCF18A4AD5CB9157977FC65E6F846285D5E306D0B64852EF2C614`.
The audio itself is not committed because it may contain private business
context. This document is the durable interpretation of the brief.

## Product outcome

The existing single-place parser becomes the data layer for SINDY, an already
functioning AI business assistant. Before a representative opens SINDY in a
salon, the system must already know the business, its local market and its
competitors.

The parser must build a city-wide catalogue, not wait for a request for one
place. For every discovered location it produces a normalized digital passport.
SINDY uses passports and competitor relationships to explain strengths,
weaknesses and growth opportunities.

```text
city + categories
  -> discovery of every visible map result
  -> source records and detailed collection
  -> identity resolution and deduplication
  -> digital passport database
  -> competitor graph and comparisons
  -> SINDY analysis API
```

## Pilot scope

- City: Yaroslavl.
- Initial categories: `ногтевая студия`, `салон красоты`,
  `студия бровей и ресниц`.
- A physical branch is a separate location.
- One location found in several categories is stored once and keeps all
  category memberships.
- Yandex Maps is primary; 2GIS is an enrichment and cross-check source.
- YCLIENTS and online-booking data remain a separate later enrichment layer.
- Telegram is the acceptance frontend. No payload is sent to SINDY until the
  city pipeline passes the acceptance criteria.

## Reusable capabilities already present

The current code already collects and normalizes one selected place from
Yandex/2GIS: identity, address, coordinates when exposed, categories, ratings,
reviews and organization replies, services grouped by map category, public
prices, opening hours, masters exposed by maps, news, awards, search rankings,
source references and a review summary. It also has partial-source tolerance,
Telegram report sections, bounded retries and project-level crash recovery.

This logic becomes a detail-enrichment worker. It must not be rewritten as the
city crawler.

## Missing system capabilities

1. Complete city/category discovery with pagination and geographic partitioning.
2. Durable PostgreSQL storage; PostGIS for spatial queries and market areas.
3. Source-record preservation and stable canonical location IDs.
4. Cross-category and cross-source entity resolution.
5. Job queue, checkpoints, incremental refresh and bounded source retries.
6. Coverage, freshness, provenance and data-quality metrics.
7. Competitor selection and a versioned comparison engine.
8. A stable machine-readable contract for the SINDY core.
9. Telegram screens for crawl control, QA, passports and comparisons.

## Digital passport contract

A passport is a versioned snapshot with at least:

- canonical business/location ID;
- name, address, coordinates, city, district and source IDs;
- all map categories and category-specific discovery evidence;
- ratings and review counts per source;
- reviews, replies, topics and summary;
- services, map category hierarchy and public prices;
- hours, masters exposed by maps, news and awards;
- website, map links and booking link;
- search positions with query, scope, timestamp and checked result count;
- completeness, freshness and collection warnings;
- `collected_at`, source provenance and change history.

Facts from different sources are not silently overwritten. The canonical value
and every supporting source record must remain distinguishable.

## Identity and deduplication rules

Resolution proceeds from strongest evidence to weakest:

1. Stable provider ID within a source.
2. Exact normalized phone, website or booking identity when available.
3. Coordinates plus normalized address.
4. Coordinates, name similarity and category compatibility.
5. Name/address similarity only creates a review candidate, not an automatic
   merge.

Branches at different addresses remain separate. Category membership is
many-to-many. Every merge stores its evidence and can be reversed. Ambiguous
matches enter a manual QA queue.

## Competitor model

Competitors are calculated relative to each location using:

- overlapping categories and services;
- distance and market scope;
- price segment;
- rating, review volume and review topics;
- search visibility for relevant queries;
- operating format and service breadth.

Small cities use city-wide scope. Large cities use districts; Moscow and Saint
Petersburg additionally use metro/transport clusters. The output contains a
score and human-readable reasons, not only a list of IDs.

AI does not invent identity, prices or rankings. Deterministic collection and
comparison produce evidence; the SINDY core turns that evidence into narrative
analysis and strategy.

## Operational boundaries

- “All businesses” means every result publicly visible to the configured map
  queries, geographic scope and collection timestamp; maps are dynamic, so an
  absolute global completeness guarantee is impossible.
- Crawl state must survive process or computer interruption without duplicate
  writes.
- Source limits, blocks and missing public fields are recorded honestly.
- Production-scale access may require contractual map access and proxy/rate
  infrastructure. The pilot must work without hiding these limitations.
- Secrets stay in `.env` or a secret store and are never committed.

## SINDY integration requirements

Preferred integration is a private versioned API. Required inputs from the SINDY
team are its repository or API base URL, authentication mechanism, payload
schema and example responses. Until supplied, the parser owns an internal
passport API and Telegram remains the validation frontend.

