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
        async def collect(
            self, query: str, city: str | None = None
        ) -> SalonProfile:
            return SalonProfile(provider="empty", provider_id="1", name=query)

    result = await SalonReportService(EmptyProvider(), max_attempts=2).create_report(
        "Unknown salon"
    )

    assert result.status == "needs_rework"
    assert result.report is not None
    assert "данная информация не предоставлена заведением публично" in result.report
    assert result.attempts == 2
    assert result.missing_fields == ["rating", "reviews", "prices"]
