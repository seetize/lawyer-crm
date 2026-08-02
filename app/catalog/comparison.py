from __future__ import annotations

from statistics import median
from typing import Any

from app.catalog.competitors import compare_locations


def build_comparison_report(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
    scope_label: str,
    limit: int = 5,
) -> str:
    eligible = [candidate for candidate in candidates if candidate["id"] != selected["id"]]
    matches = [
        match
        for candidate in eligible
        if (match := compare_locations(selected, candidate)) is not None
    ]
    matches.sort(key=lambda item: (-item.score, item.distance_km or float("inf")))
    by_id = {candidate["id"]: candidate for candidate in eligible}

    peers = [by_id[match.competitor_id] for match in matches]
    ratings = [float(peer["rating"]) for peer in peers if peer.get("rating") is not None]
    reviews = [int(peer["reviews_count"]) for peer in peers if peer.get("reviews_count") is not None]
    selected_rating = selected.get("rating")
    selected_reviews = selected.get("reviews_count")

    strengths: list[str] = []
    risks: list[str] = []
    if selected_rating is not None and ratings:
        if float(selected_rating) >= median(ratings):
            strengths.append("рейтинг не ниже медианы сопоставимых заведений")
        else:
            risks.append("рейтинг ниже медианы сопоставимых заведений")
    if selected_reviews is not None and reviews:
        if int(selected_reviews) >= median(reviews):
            strengths.append("сильная подтверждённость отзывами")
        else:
            risks.append("меньше отзывов, чем у медианного конкурента")
    service_count = len(selected.get("services") or ())
    peer_service_counts = [len(peer.get("services") or ()) for peer in peers]
    if peer_service_counts:
        if service_count >= median(peer_service_counts):
            strengths.append("ассортимент услуг не уже медианного")
        else:
            risks.append("публичный ассортимент услуг уже медианного")

    lines = [
        f"🧠 Сравнение · {selected['name']}",
        f"Зона: {scope_label}",
        f"Сопоставимых заведений: {len(matches)}",
        "",
        "Сильные стороны: " + ("; ".join(strengths) if strengths else "недостаточно данных"),
        "Зоны роста: " + ("; ".join(risks) if risks else "явных отклонений не найдено"),
    ]
    if matches:
        lines.extend(["", "Ближайшие конкуренты по профилю:"])
        for index, match in enumerate(matches[:limit], start=1):
            competitor = by_id[match.competitor_id]
            rating = competitor.get("rating")
            rating_text = f"{rating:.1f}" if isinstance(rating, (int, float)) else "нет данных"
            reasons = "; ".join(match.reasons[:3])
            lines.append(
                f"{index}. {competitor['name']} — совпадение {match.score:.0%}, "
                f"рейтинг {rating_text}\n{reasons}"
            )
    else:
        lines.extend(["", "В выбранной зоне сопоставимые заведения не найдены."])
    lines.extend(
        [
            "",
            "Сравнение рассчитано локально по сохранённым категориям, расстоянию, ",
            "услугам, рейтингу и числу отзывов. Нового парсинга не выполнялось.",
        ]
    )
    return "\n".join(lines)
