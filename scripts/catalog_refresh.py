import argparse
import asyncio
import json

from app.async_runtime import configure_asyncio_policy
from app.catalog.runtime import (
    build_catalog_repository,
    build_catalog_service,
    build_city_spec,
    build_twogis_catalog_service,
)
from app.config import Settings


async def run(force: bool, discovery_only: bool) -> dict:
    settings = Settings()
    repository = build_catalog_repository(settings)
    areas_backfilled = await asyncio.to_thread(repository.backfill_profile_areas)
    service = build_catalog_service(settings, repository)
    summaries = await service.crawl_city(
        build_city_spec(settings),
        settings.catalog_category_list(),
        force=force,
    )
    twogis_service = build_twogis_catalog_service(settings, repository)
    if twogis_service is not None:
        summaries.extend(
            await twogis_service.crawl_city(
                build_city_spec(settings),
                settings.catalog_category_list(),
                force=force,
            )
        )
    enrichment = {"completed": 0, "failed": 0}
    competitors = 0
    if not discovery_only:
        enrichment = await service.enrich_pending(settings.catalog_enrich_batch_size)
        competitors = await service.rebuild_competitors(settings.catalog_city)
    cleanup = await asyncio.to_thread(repository.cleanup)
    return {
        "crawls": [summary.model_dump(mode="json") for summary in summaries],
        "enrichment": enrichment,
        "competitor_edges": competitors,
        "cleanup": cleanup,
        "areas_backfilled": areas_backfilled,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--discovery-only", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(run(arguments.force, arguments.discovery_only)),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    configure_asyncio_policy()
    raise SystemExit(main())
