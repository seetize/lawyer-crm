import argparse
from pathlib import Path

from app.catalog.excel_export import build_profile_xlsx
from app.catalog.runtime import build_catalog_repository
from app.config import Settings
from app.models import SalonProfile, SourceRating, SourceRef


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Название или ID заведения")
    parser.add_argument("--output", default="outputs/catalog_salon.xlsx")
    args = parser.parse_args()
    settings = Settings()
    repository = build_catalog_repository(settings)
    location = repository.get_location(args.query)
    if location is None:
        matches = repository.list_locations(city_name=settings.catalog_city, query=args.query, limit=1)
        location = repository.get_location(matches[0]["id"]) if matches else None
    if location is None:
        raise SystemExit("Заведение не найдено")
    if location.get("profile"):
        profile = SalonProfile.model_validate(location["profile"])
    else:
        sources = location.get("sources") or []
        profile = SalonProfile(
            provider="+".join(str(x.get("provider")) for x in sources),
            provider_id=str(sources[0].get("provider_id") if sources else location["id"]),
            name=location["name"], address=location.get("address"), city=location.get("city"),
            latitude=location.get("latitude"), longitude=location.get("longitude"),
            categories=location.get("categories") or [],
            ratings=[SourceRating(provider=str(x.get("provider")), rating=x.get("rating"), reviews_count=x.get("reviews_count"), url=x.get("url")) for x in sources],
            sources=[SourceRef(provider=str(x.get("provider")), provider_id=x.get("provider_id"), url=x.get("url")) for x in sources],
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_profile_xlsx(profile))
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
