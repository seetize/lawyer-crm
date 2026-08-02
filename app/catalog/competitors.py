from __future__ import annotations

import math
from itertools import permutations
from typing import Any

from app.catalog.db import haversine_km
from app.catalog.domain import CompetitorMatch


ALGORITHM_VERSION = "v1"


def compute_competitors(
    locations: list[dict[str, Any]],
    *,
    limit_per_location: int = 20,
) -> list[CompetitorMatch]:
    candidates: dict[str, list[CompetitorMatch]] = {
        location["id"]: [] for location in locations
    }
    for left, right in permutations(locations, 2):
        match = compare_locations(left, right)
        if match is not None:
            candidates[left["id"]].append(match)
    result: list[CompetitorMatch] = []
    for location_id, matches in candidates.items():
        matches.sort(key=lambda item: (-item.score, item.distance_km or math.inf, item.competitor_id))
        result.extend(matches[:limit_per_location])
    return result


def compare_locations(
    left: dict[str, Any],
    right: dict[str, Any],
) -> CompetitorMatch | None:
    categories_left = set(left.get("categories") or ())
    categories_right = set(right.get("categories") or ())
    category_union = categories_left | categories_right
    category_overlap = (
        len(categories_left & categories_right) / len(category_union)
        if category_union
        else 0.0
    )
    services_left = set(left.get("services") or ())
    services_right = set(right.get("services") or ())
    service_union = services_left | services_right
    service_overlap = (
        len(services_left & services_right) / len(service_union)
        if service_union
        else 0.0
    )
    distance = haversine_km(
        left.get("latitude"),
        left.get("longitude"),
        right.get("latitude"),
        right.get("longitude"),
    )
    proximity = 0.5 if math.isinf(distance) else max(0.0, 1 - distance / 15)
    rating_similarity = _numeric_similarity(left.get("rating"), right.get("rating"), 5)
    review_similarity = _log_similarity(
        left.get("reviews_count"), right.get("reviews_count")
    )
    score = (
        category_overlap * 0.45
        + proximity * 0.25
        + service_overlap * 0.15
        + rating_similarity * 0.08
        + review_similarity * 0.07
    )
    if category_overlap == 0 or score < 0.3:
        return None
    reasons: list[str] = []
    common_categories = sorted(categories_left & categories_right)
    if common_categories:
        reasons.append("Общие категории: " + ", ".join(common_categories[:3]))
    if not math.isinf(distance):
        reasons.append(f"Расстояние: {distance:.1f} км")
    if service_overlap:
        reasons.append(f"Пересечение услуг: {round(service_overlap * 100)}%")
    if rating_similarity >= 0.8:
        reasons.append("Сопоставимый рейтинг")
    return CompetitorMatch(
        location_id=left["id"],
        competitor_id=right["id"],
        score=round(score, 4),
        distance_km=(None if math.isinf(distance) else round(distance, 3)),
        reasons=reasons,
    )


def _numeric_similarity(left: Any, right: Any, scale: float) -> float:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return 0.5
    return max(0.0, 1 - abs(float(left) - float(right)) / scale)


def _log_similarity(left: Any, right: Any) -> float:
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return 0.5
    return max(0.0, 1 - abs(math.log1p(left) - math.log1p(right)) / 8)

