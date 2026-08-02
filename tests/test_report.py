import pytest

from app.providers.demo import DemoPlaceProvider
from app.report import build_report
from app.service import SalonReportService


@pytest.mark.asyncio
async def test_demo_report_contains_requested_sections() -> None:
    result = await SalonReportService(DemoPlaceProvider()).create_report(
        "Beauty House", ["рейтинг", "услуги"]
    )

    assert "Beauty House" in result.report
    assert "⭐ Рейтинг: 4.7/5" in result.report
    assert "✂️ Услуги:" in result.report
    assert "Мастера:" not in result.report
    assert result.status == "ready"
    assert result.attempts == 1


def test_missing_data_is_explicit() -> None:
    profile = pytest.importorskip("app.models").SalonProfile(
        provider="test", provider_id="1", name="Salon"
    )

    report = build_report(profile)

    assert "данная информация не предоставлена заведением публично" in report


@pytest.mark.asyncio
async def test_incomplete_profile_is_not_published() -> None:
    from app.models import SalonProfile
    from app.providers.base import PlaceProvider

    class EmptyProvider(PlaceProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def collect(
            self, query: str, city: str | None = None
        ) -> SalonProfile:
            self.calls += 1
            return SalonProfile(provider="empty", provider_id="1", name=query)

    provider = EmptyProvider()
    result = await SalonReportService(provider, max_attempts=2).create_report(
        "Unknown salon"
    )

    assert result.status == "needs_rework"
    assert result.report is not None
    assert "данная информация не предоставлена заведением публично" in result.report
    assert result.attempts == 2
    assert result.missing_fields == ["rating", "reviews", "prices"]
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_targeted_rework_uses_previous_profile_without_full_recollect() -> None:
    from app.models import Review, SalonProfile, Service
    from app.providers.base import PlaceProvider

    class TargetedProvider(PlaceProvider):
        def __init__(self) -> None:
            self.collect_calls = 0
            self.rework_calls = 0
            self.received_missing: list[str] = []
            self.received_previous: SalonProfile | None = None

        async def collect(
            self, query: str, city: str | None = None
        ) -> SalonProfile:
            self.collect_calls += 1
            return SalonProfile(provider="targeted", provider_id="1", name=query)

        async def recollect_missing(
            self,
            query: str,
            city: str | None,
            missing_fields: list[str],
            previous: SalonProfile,
        ) -> SalonProfile | None:
            self.rework_calls += 1
            self.received_missing = missing_fields
            self.received_previous = previous
            return SalonProfile(
                provider="targeted",
                provider_id="1",
                name=query,
                rating=4.8,
                reviews=[Review(author="Анна", rating=5, text="Отлично")],
                services=[Service(name="Маникюр", price="1000 ₽")],
            )

    provider = TargetedProvider()
    result = await SalonReportService(provider, max_attempts=2).create_report(
        "Target salon"
    )

    assert result.status == "ready"
    assert result.attempts == 2
    assert provider.collect_calls == 1
    assert provider.rework_calls == 1
    assert provider.received_missing == ["rating", "reviews", "prices"]
    assert provider.received_previous is not None
    assert result.profile.rating == 4.8
    assert result.profile.services[0].price == "1000 ₽"
