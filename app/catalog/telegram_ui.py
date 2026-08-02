from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def category_keyboard(categories: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{item['name']} · {item['count']}",
                callback_data=f"cg:{item['id']}",
            )
        ]
        for item in categories
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def scope_keyboard(has_districts: bool, has_metro: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🏙 Весь город", callback_data="cs:city")]]
    if has_districts:
        rows.append([InlineKeyboardButton(text="📍 Район", callback_data="cs:district")])
    if has_metro:
        rows.append([InlineKeyboardButton(text="🚇 Станция метро", callback_data="cs:metro")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def zones_keyboard(
    zones: list[dict],
    token: str,
    zone_type: str,
) -> InlineKeyboardMarkup:
    icon = "🚇" if zone_type == "metro" else "📍"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{icon} {zone['name']} · {zone['count']}",
                callback_data=f"cz:{token}:{index}",
            )
        ]
        for index, zone in enumerate(zones)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="✍️ Написать название",
                callback_data=f"ct:{token}:{zone_type}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def locations_keyboard(
    locations: list[dict],
    token: str,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows = []
    for location in locations:
        rating = location.get("rating")
        suffix = f" · ⭐ {rating:.1f}" if isinstance(rating, (int, float)) else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=(location["name"] + suffix)[:60],
                    callback_data=f"cl:{location['id']}",
                )
            ]
        )
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text="← Назад", callback_data=f"cp:{token}:{page - 1}")
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(text="Далее →", callback_data=f"cp:{token}:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comparison_scope_keyboard(
    location_id: str,
    has_districts: bool,
    has_metro: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🏙 Весь город",
                callback_data=f"cms:city:{location_id}",
            )
        ]
    ]
    if has_districts:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📍 Район",
                    callback_data=f"cms:district:{location_id}",
                )
            ]
        )
    if has_metro:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚇 Станция метро",
                    callback_data=f"cms:metro:{location_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comparison_back_keyboard(location_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← К карточке",
                    callback_data=f"cl:{location_id}",
                )
            ]
        ]
    )


def stored_card_text(location: dict) -> str:
    rating = location.get("rating")
    rating_text = f"{rating:.1f}/5" if isinstance(rating, (int, float)) else "не опубликован"
    reviews = location.get("reviews_count")
    review_text = str(reviews) if reviews is not None else "не опубликовано"
    zones = []
    if location.get("district"):
        zones.append(f"район {location['district']}")
    if location.get("metro"):
        zones.append(f"метро {location['metro']}")
    return "\n".join(
        [
            f"🏢 {location['name']}",
            f"📍 {location.get('address') or 'адрес не опубликован'}",
            f"⭐ Оценка: {rating_text}",
            f"💬 Отзывов/оценок: {review_text}",
            f"🏷 Категории: {', '.join(location.get('categories') or ()) or 'не указаны'}",
            f"🗺 Зона: {', '.join(zones) or 'не определена'}",
            "",
            "Подробный паспорт ещё обновляется в фоне. Каталог показан из сохранённой БД.",
        ]
    )
