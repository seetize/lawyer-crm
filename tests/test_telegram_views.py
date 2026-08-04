from app.models import (
    FeatureItem,
    NewsItem,
    OrganizationReply,
    Review,
    SalonProfile,
    SearchRanking,
    Service,
)
from app.telegram_views import render_section, section_keyboard


def test_all_reviews_and_replies_are_available_across_pages() -> None:
    reviews = [
        Review(
            provider="yandex_maps",
            provider_review_id=f"id-{index}",
            author=f"Автор {index}",
            rating=5,
            text=f"Уникальный текст отзыва {index} " + "x" * 300,
            organization_replies=[
                OrganizationReply(text=f"Уникальный ответ компании {index}")
            ],
        )
        for index in range(40)
    ]
    profile = SalonProfile(
        provider="yandex_maps",
        provider_id="1",
        primary_provider="yandex_maps",
        name="Salon",
        reviews=reviews,
    )

    first = render_section(profile, "reviews", 0)
    pages = [
        render_section(profile, "reviews", page).text
        for page in range(first.total_pages)
    ]
    combined = "\n".join(pages)

    assert first.total_pages > 1
    assert all(len(page) <= 3800 for page in pages)
    for index in range(40):
        assert f"Уникальный текст отзыва {index}" in combined
        assert f"Уникальный ответ компании {index}" in combined


def test_callback_payloads_are_short() -> None:
    keyboard = section_keyboard(
        "abcdefghijk",
        "reviews",
        2,
        10,
        compare_location_id="12345678-1234-1234-1234-123456789012",
    )

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert callbacks
    assert all(len(value.encode()) <= 64 for value in callbacks)
    assert any(value.startswith("cmp:") for value in callbacks)


def test_new_yandex_sections_are_rendered() -> None:
    profile = SalonProfile(
        provider="yandex_maps",
        provider_id="1",
        name="Salon",
        categories=["Ногтевая студия"],
        awards=["Хорошее место 2026"],
        masters=[],
        services=[Service(name="Маникюр", category="Ногти", price="1000 ₽")],
        news=[NewsItem(provider_news_id="42", text="Открыли новый кабинет")],
        features=[
            FeatureItem(name="Wi-Fi", value="True", category="Удобства"),
            FeatureItem(name="Парковка", value="False", category="Удобства"),
        ],
        search_rankings=[
            SearchRanking(
                query="ногтевая студия",
                position=3,
                total_results=50,
                checked_results=25,
                scope="Астрахань",
                scope_type="city",
            )
        ],
    )

    assert "Хорошее место 2026" in render_section(profile, "main").text
    assert "📂 Ногти" in render_section(profile, "services").text
    assert "Открыли новый кабинет" in render_section(profile, "news").text
    features = render_section(profile, "features").text
    assert features.count("📂 Удобства") == 1
    assert "Wi-Fi: Да" in features
    assert "3-е место" in render_section(profile, "rankings").text
    assert "не предоставлена" in render_section(profile, "masters").text
