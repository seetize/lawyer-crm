from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

from app.catalog.competitors import compare_locations


REVIEW_THEMES = {
    "качество работ": ("качеств", "результат", "маник", "стриж", "окраш"),
    "мастера": ("мастер", "специалист", "профессион"),
    "сервис": ("персонал", "администратор", "вежлив", "отношение"),
    "обстановка": ("чист", "уют", "атмосфер", "интерьер"),
    "ожидание": ("долго", "ждал", "ожидан", "опозд", "очеред"),
    "цены": ("цен", "дорог", "стоим", "дешев"),
}
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

    lines = [
        f"🧠 Аналитическое сравнение · {selected['name']}",
        f"Зона: {scope_label}. Сопоставимых заведений: {len(matches)}.",
        _reference_summary(selected, peers),
    ]
    selected_review = _review_observation(selected)
    if selected_review:
        lines.append(f"По отзывам точки отсчёта: {selected_review}")

    if not matches:
        lines.extend(
            [
                "",
                "В выбранной зоне нет заведений с совпадающими категориями, поэтому "
                "сильные и слабые стороны относительно конкурентов не рассчитывались.",
            ]
        )
    else:
        lines.extend(["", "Сравнение с ближайшими конкурентами:"])
        for index, match in enumerate(matches[:limit], start=1):
            competitor = by_id[match.competitor_id]
            stronger, weaker, unknown = _relative_points(selected, competitor)
            facts = _fact_line(selected, competitor, match.distance_km)
            lines.extend(
                [
                    "",
                    f"{index}. {competitor['name']} — {facts}",
                    "Сильнее точки отсчёта: " + ("; ".join(stronger) or "не выявлено по доступным данным"),
                    "Слабее точки отсчёта: " + ("; ".join(weaker) or "не выявлено по доступным данным"),
                ]
            )
            review = _review_observation(competitor)
            lines.append(
                "Отзывы: "
                + (review or "тексты отзывов ещё не сохранены, вывод по содержанию не делался")
            )
            if unknown:
                lines.append("Не оценивалось: " + "; ".join(unknown) + ".")

    lines.extend(
        [
            "",
            "Вывод построен локальным аналитическим ассистентом только по сохранённым "
            "картам, отзывам и услугам. Отсутствующие сведения не считаются нулём; "
            "новый парсинг при нажатии не запускается.",
        ]
    )
    return "\n".join(lines)


def _reference_summary(selected: dict[str, Any], peers: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    rating = selected.get("rating")
    ratings = [float(peer["rating"]) for peer in peers if peer.get("rating") is not None]
    if isinstance(rating, (int, float)):
        text = f"рейтинг {float(rating):.1f}"
        if ratings:
            delta = float(rating) - median(ratings)
            text += f" ({_delta_text(delta, 'медианы группы')})"
        parts.append(text)
    else:
        parts.append("рейтинг не опубликован")
    reviews = selected.get("reviews_count")
    review_counts = [
        int(peer["reviews_count"])
        for peer in peers
        if peer.get("reviews_count") is not None
    ]
    if isinstance(reviews, int):
        text = f"{reviews} отзывов/оценок"
        if review_counts:
            text += f" (медиана группы {median(review_counts):g})"
        parts.append(text)
    else:
        parts.append("число отзывов не опубликовано")
    return "Точка отсчёта: " + "; ".join(parts) + "."


def _fact_line(
    selected: dict[str, Any],
    competitor: dict[str, Any],
    distance_km: float | None,
) -> str:
    facts = []
    rating = competitor.get("rating")
    selected_rating = selected.get("rating")
    if isinstance(rating, (int, float)):
        rating_text = f"оценка {float(rating):.1f}"
        if isinstance(selected_rating, (int, float)):
            rating_text += f" ({_delta_text(float(rating) - float(selected_rating), 'точки отсчёта')})"
        facts.append(rating_text)
    else:
        facts.append("оценка не опубликована")
    reviews = competitor.get("reviews_count")
    facts.append(
        f"{reviews} отзывов/оценок"
        if isinstance(reviews, int)
        else "число отзывов не опубликовано"
    )
    if distance_km is not None:
        facts.append(f"{distance_km:.1f} км от точки")
    return ", ".join(facts) + "."


def _relative_points(
    selected: dict[str, Any],
    competitor: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    stronger: list[str] = []
    weaker: list[str] = []
    unknown: list[str] = []
    selected_rating = selected.get("rating")
    rating = competitor.get("rating")
    if isinstance(selected_rating, (int, float)) and isinstance(rating, (int, float)):
        delta = float(rating) - float(selected_rating)
        if delta >= 0.05:
            stronger.append(f"рейтинг выше на {delta:.1f}")
        elif delta <= -0.05:
            weaker.append(f"рейтинг ниже на {abs(delta):.1f}")
    else:
        unknown.append("сравнение рейтинга")

    selected_reviews = selected.get("reviews_count")
    reviews = competitor.get("reviews_count")
    if isinstance(selected_reviews, int) and isinstance(reviews, int):
        if reviews > selected_reviews:
            stronger.append(f"больше подтверждений отзывами: {reviews} против {selected_reviews}")
        elif reviews < selected_reviews:
            weaker.append(f"меньше подтверждений отзывами: {reviews} против {selected_reviews}")
    else:
        unknown.append("сравнение количества отзывов")

    selected_services = set(selected.get("services") or ())
    services = set(competitor.get("services") or ())
    if selected_services and services:
        extra = sorted(services - selected_services)
        missing = sorted(selected_services - services)
        if extra:
            stronger.append("дополнительные публичные услуги: " + ", ".join(extra[:3]))
        if missing:
            weaker.append("не найдены услуги точки отсчёта: " + ", ".join(missing[:3]))
    else:
        unknown.append("ассортимент услуг — нет сопоставимых публичных прайсов")
    return stronger, weaker, unknown


def _review_observation(location: dict[str, Any]) -> str | None:
    summary = " ".join(str(location.get("reviews_summary") or "").split())
    if summary:
        return summary[:320].rstrip(" ,.;") + ("…" if len(summary) > 320 else ".")
    texts = [" ".join(str(text).casefold().split()) for text in location.get("review_texts") or ()]
    if not texts:
        return None
    mentions: Counter[str] = Counter()
    for text in texts:
        for theme, markers in REVIEW_THEMES.items():
            if any(marker in text for marker in markers):
                mentions[theme] += 1
    repeated = [name for name, count in mentions.most_common(3) if count > 1]
    if repeated:
        return "повторяющиеся темы: " + ", ".join(repeated)
    if mentions:
        return "в единичных отзывах упоминаются: " + ", ".join(
            name for name, _ in mentions.most_common(3)
        )
    return f"доступно {len(texts)} текстов без устойчивой повторяющейся темы"


def _delta_text(delta: float, target: str) -> str:
    if abs(delta) < 0.05:
        return f"на уровне {target}"
    direction = "выше" if delta > 0 else "ниже"
    return f"на {abs(delta):.1f} {direction} {target}"
