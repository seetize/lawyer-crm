# SINDY City Parser — Telegram acceptance plan

Telegram is the pre-integration QA frontend. The parser is not connected to the
SINDY production core while a known critical defect remains in these scenarios.
“No errors” means no known reproducible critical/high defects under this plan,
not a promise that external map sources can never change.

## Required Telegram views

1. City and category selection.
2. Crawl start/status with discovered, unique, enriched, failed and pending
   counts.
3. City catalogue search and filters.
4. Digital passport sections.
5. Source evidence, freshness and missing-data warnings.
6. Duplicate candidates and reversible merge decision.
7. Competitor list with explanation factors.
8. Pairwise comparison and city/category benchmarks.

## Pilot test matrix

### Discovery coverage

- Run all three Yaroslavl categories with fixed search scopes and timestamp.
- Compare stored counts with the visible Yandex/2GIS baseline for the same
  scopes.
- Account for every page/partition as completed, empty, blocked or failed.
- Report coverage uncertainty instead of claiming invisible results.

### Identity quality

- Manually inspect at least 30 locations across all categories.
- Verify every multi-category overlap is one location with many memberships.
- Verify sampled branches at different addresses remain separate.
- Require zero incorrect automatic merges in the control sample.

### Passport quality

- Compare at least 20 passports against their live map cards.
- Check identity, address, categories, ratings, review/reply association,
  services, price text, hours, news, awards and source links.
- Missing public information must be labelled as unavailable, not fabricated.
- Every material fact must show source and collection time.

### Recovery and idempotency

- Stop discovery, enrichment and deduplication at controlled checkpoints.
- Restart each process and confirm continuation without lost records.
- Repeat the same crawl and confirm no duplicate locations, reviews or category
  memberships.
- Simulate one source failure and confirm valid data from another source remains.

### Competitor quality

- Review competitor lists for at least 10 salons from different segments.
- Confirm category, geography and price reasoning is visible.
- Confirm obvious distant or incompatible businesses are excluded.
- Recalculate with identical inputs and require identical deterministic scores.

### SINDY contract gate

- Use staging credentials only.
- Validate the exact JSON schema and idempotency key.
- Send a fixed passport fixture and compare facts with Telegram.
- Verify stale/incomplete passports carry warnings.
- Verify SINDY narrative claims can be traced to supplied evidence.

## Release gate to SINDY frontend

Integration may proceed when:

- the full pilot crawl completes and resumes after interruption;
- the control samples pass identity and passport checks;
- there are no known critical/high defects;
- source blocks and missing fields degrade honestly;
- repeated runs are idempotent;
- competitor explanations pass manual review;
- database backup/restore and migration rollback are tested;
- the SINDY staging contract test passes.

Any later source-contract regression returns the affected capability to Telegram
QA before its new data is promoted to SINDY.
