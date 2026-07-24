from app.models import SalonProfile

ALL_CRITERIA = {"rating", "reviews", "prices", "services", "slots", "masters"}
NOT_PUBLIC = "данная информация не предоставлена заведением публично"
ALIASES = {
    "оценка": "rating",
    "рейтинг": "rating",
    "отзывы": "reviews",
    "цены": "prices",
    "услуги": "services",
    "время": "slots",
    "слоты": "slots",
    "мастера": "masters",
}


def normalize_criteria(criteria: list[str]) -> set[str]:
    if not criteria:
        return ALL_CRITERIA
    return {ALIASES.get(item.strip().lower(), item.strip().lower()) for item in criteria}


def build_report(profile: SalonProfile, criteria: list[str] | None = None) -> str:
    selected = normalize_criteria(criteria or [])
    lines = [f"🏢 {profile.name}"]
    if profile.address:
        lines.append(f"📍 {profile.address}")
    summary = _summary(profile)
    lines.extend(["", f"Кратко: {summary}", "", "Критерии:"])

    if "rating" in selected:
        rating = f"{profile.rating:.1f}/5" if profile.rating is not None else NOT_PUBLIC
        count = f" ({profile.reviews_count} оценок)" if profile.reviews_count else ""
        lines.append(f"⭐ Рейтинг: {rating}{count}")
    if "prices" in selected:
        if profile.price_level:
            price_info = profile.price_level
        elif any(service.price for service in profile.services):
            price_info = "конкретные цены указаны в списке услуг ниже"
        else:
            price_info = NOT_PUBLIC
        lines.append(f"💳 Цены: {price_info}")
    if "services" in selected:
        lines.extend(_services(profile))
    if "masters" in selected:
        lines.append("👤 Мастера: " + (", ".join(profile.masters) or NOT_PUBLIC))
    if "slots" in selected:
        lines.append(
            "🗓 Свободное время: "
            + (", ".join(profile.available_slots) or NOT_PUBLIC)
        )
    if "reviews" in selected:
        lines.extend(_reviews(profile))
    if profile.opening_hours:
        lines.extend(["", "🕒 Часы работы:", *profile.opening_hours])
    if profile.map_url:
        lines.extend(["", f"Источник: {profile.map_url}"])
    elif profile.sources:
        lines.extend(
            ["", "Источники:", *(f"• {source.provider}" for source in profile.sources)]
        )
    return "\n".join(lines)


def _summary(profile: SalonProfile) -> str:
    parts = []
    if profile.rating is not None:
        parts.append(f"оценка {profile.rating:.1f} из 5")
    if profile.reviews_count:
        parts.append(f"{profile.reviews_count} оценок")
    if profile.price_level:
        parts.append(f"{profile.price_level} ценовой уровень")
    return "; ".join(parts).capitalize() + "." if parts else "Недостаточно данных."


def _services(profile: SalonProfile) -> list[str]:
    if not profile.services:
        return [f"✂️ Услуги и конкретные цены: {NOT_PUBLIC}"]
    rendered = []
    visible_services = profile.services[:20]
    for service in visible_services:
        details = " · ".join(value for value in [service.price, service.duration] if value)
        rendered.append(f"  • {service.name}" + (f" — {details}" if details else ""))
    if len(profile.services) > len(visible_services):
        rendered.append(f"  • …и ещё {len(profile.services) - len(visible_services)} услуг")
    return ["✂️ Услуги:", *rendered]


def _reviews(profile: SalonProfile) -> list[str]:
    if not profile.reviews:
        return [f"💬 Отзывы: {NOT_PUBLIC}"]
    result = ["💬 Последние доступные отзывы:"]
    for review in profile.reviews[:3]:
        rating = f", {review.rating:g}/5" if review.rating is not None else ""
        text = review.text.replace("\n", " ").strip()
        if len(text) > 240:
            text = text[:237] + "..."
        result.append(f'  • {review.author}{rating}: “{text}”')
    return result
