import io
import zipfile

from app.catalog.excel_export import build_profile_xlsx
from app.models import (
    BranchRef,
    FeatureItem,
    MediaItem,
    OrganizationReply,
    Review,
    SalonProfile,
    StoryItem,
)


def test_profile_export_is_valid_normalized_xlsx() -> None:
    profile = SalonProfile(
        provider="yandex_maps+2gis",
        provider_id="1",
        name="Студия Лак",
        features=[FeatureItem(name="Wi-Fi", value="да", provider="yandex_maps")],
        stories=[StoryItem(provider_story_id="s1", title="Новинка", category="Услуги")],
        branches=[BranchRef(provider_id="b1", name="Филиал", address="ул. Кирова, 1")],
        media=[MediaItem(provider_media_id="m1", url="https://example.test/photo.jpg")],
        phones=["+7 999 000-00-00"],
        metro_stations=["Площадь"],
        masters=["Анна"],
        available_slots=["2026-08-06 10:00"],
        reviews_summary="Сильный сервис",
        source_payloads={"yandex_maps": {"nested": {"hidden_field": "raw-value"}}},
        reviews=[
            Review(
                author="Клиент",
                text="Отзыв клиента",
                organization_replies=[
                    OrganizationReply(
                        author="Салон",
                        published_at="2026-08-01",
                        text="Ответ салона",
                    )
                ],
            )
        ],
    )

    content = build_profile_xlsx(profile)
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
        assert workbook.testzip() is None
        xml = workbook.read("xl/workbook.xml").decode("utf-8")
        assert "Филиалы" in xml
        assert "Особенности" in xml
        assert "Истории" in xml
        assert "Медиа" in xml
        assert "Телефоны" in xml
        assert "Метро" in xml
        assert "Мастера" in xml
        assert "Окна записи" in xml
        assert "Полный паспорт" in xml
        assert "Сырые данные" in xml
        worksheet_text = "\n".join(
            workbook.read(name).decode("utf-8")
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/")
        )
        assert "raw-value" in worksheet_text
        assert "Сильный сервис" in worksheet_text
        assert "Отзыв клиента" in worksheet_text
        assert "Ответ салона" in worksheet_text
        assert "Ответы" not in xml
