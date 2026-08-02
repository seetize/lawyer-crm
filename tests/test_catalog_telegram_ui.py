from app.catalog.telegram_ui import (
    category_keyboard,
    comparison_scope_keyboard,
    locations_keyboard,
    zones_keyboard,
)


def _callback_values(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_catalog_callback_payloads_fit_telegram_limit() -> None:
    location_id = "12345678-1234-1234-1234-123456789012"
    markups = [
        category_keyboard([{"id": location_id, "name": "Салон красоты", "count": 10}]),
        zones_keyboard([{"name": "Очень длинный район", "count": 10}], "abcdef", "district"),
        locations_keyboard([{"id": location_id, "name": "Студия", "rating": 4.8}], "abcdef", 0, True),
        comparison_scope_keyboard(location_id, True, True),
    ]

    callbacks = [value for markup in markups for value in _callback_values(markup)]
    assert callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)
