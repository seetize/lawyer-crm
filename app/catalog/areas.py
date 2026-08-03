from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.catalog.db import CatalogRepository
from app.catalog.domain import CitySpec


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"
USER_AGENT = "BeautyInspector-MVP/0.1 (district catalogue)"


@dataclass(frozen=True)
class DistrictBoundary:
    name: str
    polygons: tuple[tuple[tuple[float, float], ...], ...]


class OpenStreetMapDistrictResolver:
    """Resolve official city districts once and persist point memberships."""

    async def resolve(self, city: CitySpec) -> list[DistrictBoundary]:
        scope = city.scope
        query = (
            '[out:json][timeout:25];relation["boundary"="administrative"]'
            '["admin_level"~"8|9|10"]'
            f"({scope.south},{scope.west},{scope.north},{scope.east});"
            "out ids tags center;"
        )
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(timeout=45, headers=headers) as client:
            overpass = await client.post(OVERPASS_URL, content=query.encode("utf-8"))
            overpass.raise_for_status()
            relations = [
                item
                for item in overpass.json().get("elements", [])
                if item.get("type") == "relation"
                and _is_district_name(str(item.get("tags", {}).get("name") or ""))
            ][:50]
            if not relations:
                return []
            osm_ids = ",".join(f"R{item['id']}" for item in relations)
            lookup = await client.get(
                NOMINATIM_URL,
                params={
                    "osm_ids": osm_ids,
                    "format": "jsonv2",
                    "polygon_geojson": 1,
                },
            )
            lookup.raise_for_status()
        return [
            boundary
            for item in lookup.json()
            if (boundary := _parse_boundary(item)) is not None
        ]

    async def refresh(
        self,
        repository: CatalogRepository,
        city: CitySpec,
    ) -> dict[str, int]:
        boundaries = await self.resolve(city)
        if not boundaries:
            raise RuntimeError("No validated district boundaries returned")
        locations = repository.coordinate_locations(city.name)
        assignments = assign_districts(locations, boundaries)
        assigned = repository.replace_boundary_districts(city.name, assignments)
        return {
            "boundaries": len(boundaries),
            "locations": len(locations),
            "assigned": assigned,
        }


def assign_districts(
    locations: list[dict[str, Any]],
    boundaries: list[DistrictBoundary],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for location in locations:
        point = (float(location["longitude"]), float(location["latitude"]))
        for boundary in boundaries:
            if any(_point_in_polygon(point, polygon) for polygon in boundary.polygons):
                result[str(location["id"])] = boundary.name
                break
    return result


def _parse_boundary(item: dict[str, Any]) -> DistrictBoundary | None:
    name = " ".join(str(item.get("name") or item.get("display_name") or "").split())
    geojson = item.get("geojson") if isinstance(item.get("geojson"), dict) else {}
    geometry_type = geojson.get("type")
    coordinates = geojson.get("coordinates")
    if not name or not isinstance(coordinates, list):
        return None
    if geometry_type == "Polygon":
        raw_polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        raw_polygons = coordinates
    else:
        return None
    polygons = []
    for polygon in raw_polygons:
        if not isinstance(polygon, list) or not polygon:
            continue
        outer = polygon[0]
        if not isinstance(outer, list) or len(outer) < 3:
            continue
        points = tuple(
            (float(point[0]), float(point[1]))
            for point in outer
            if isinstance(point, list) and len(point) >= 2
        )
        if len(points) >= 3:
            polygons.append(points)
    if not polygons:
        return None
    return DistrictBoundary(name=name, polygons=tuple(polygons))


def _is_district_name(name: str) -> bool:
    normalized = name.casefold().replace("ё", "е")
    return normalized.endswith(" район") or normalized.endswith(" district")


def _point_in_polygon(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            boundary_x = (previous_x - current_x) * (y - current_y) / (
                previous_y - current_y
            ) + current_x
            if x < boundary_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside
