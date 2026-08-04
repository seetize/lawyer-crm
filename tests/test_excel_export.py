import io
import zipfile

from app.catalog.excel_export import build_profile_xlsx
from app.models import BranchRef, FeatureItem, SalonProfile, StoryItem


def test_profile_export_is_valid_normalized_xlsx() -> None:
    profile = SalonProfile(
        provider="yandex_maps+2gis",
        provider_id="1",
        name="Студия Лак",
        features=[FeatureItem(name="Wi-Fi", value="да", provider="yandex_maps")],
        stories=[StoryItem(provider_story_id="s1", title="Новинка", category="Услуги")],
        branches=[BranchRef(provider_id="b1", name="Филиал", address="ул. Кирова, 1")],
    )

    content = build_profile_xlsx(profile)
    with zipfile.ZipFile(io.BytesIO(content)) as workbook:
        assert workbook.testzip() is None
        xml = workbook.read("xl/workbook.xml").decode("utf-8")
        assert "Филиалы" in xml
        assert "Особенности" in xml
        assert "Истории" in xml
