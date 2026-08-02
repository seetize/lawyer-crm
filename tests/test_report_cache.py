import pytest

from app.models import SalonProfile
from app.report_cache import MemoryReportCache


@pytest.mark.asyncio
async def test_cache_checks_owner_and_ttl() -> None:
    now = [100.0]
    cache = MemoryReportCache(
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    profile = SalonProfile(provider="test", provider_id="1", name="Salon")
    token = await cache.put(42, profile, location_id="location-1")

    assert await cache.get(token, 42) is profile
    assert (await cache.get_view(token, 42)).location_id == "location-1"
    assert await cache.get(token, 7) is None

    now[0] = 111.0
    assert await cache.get(token, 42) is None
