# Verified failure lessons

## Duplicate collection without a new strategy

Symptom: a report missing required public fields may repeat the same complete
network collection, increasing latency and requests without discovering new data.

Rule: a rework attempt must identify a different source, endpoint, or hypothesis.
Never repeat an identical full collection solely because a public field is absent.

Fix: `PlaceProvider.recollect_missing` is an explicit opt-in hook. Without a
different targeted strategy, `CollectorAgent` reuses the previous profile.

Regression evidence: `test_incomplete_profile_is_not_published` asserts that two
review attempts result in exactly one complete provider call.
