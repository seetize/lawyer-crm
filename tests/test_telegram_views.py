from app.models import OrganizationReply, Review, SalonProfile
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
    keyboard = section_keyboard("abcdefghijk", "reviews", 2, 10)

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert callbacks
    assert all(len(value.encode()) <= 64 for value in callbacks)
