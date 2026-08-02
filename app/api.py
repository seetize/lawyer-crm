import asyncio
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query

from app.config import Settings, get_settings
from app.agents.contracts import AgentRunResult
from app.models import ReportRequest
from app.providers import build_provider
from app.providers.base import PlaceNotFoundError
from app.service import SalonReportService
from app.review_summary import build_review_summarizer
from app.catalog.db import CatalogRepository
from app.catalog.runtime import build_catalog_repository

app = FastAPI(title="Beauty Inspector API", version="0.1.0")


def get_service(settings: Settings = Depends(get_settings)) -> SalonReportService:
    return SalonReportService(
        build_provider(settings),
        review_summarizer=build_review_summarizer(settings),
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@lru_cache
def get_catalog_repository() -> CatalogRepository:
    return build_catalog_repository(get_settings())


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready(
    repository: CatalogRepository = Depends(get_catalog_repository),
) -> dict[str, str]:
    try:
        await asyncio.to_thread(repository.health)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Catalog database is unavailable") from error
    return {"status": "ready"}


@app.get("/v1/catalog/status")
async def catalog_status(
    city: str | None = None,
    repository: CatalogRepository = Depends(get_catalog_repository),
) -> dict:
    return await asyncio.to_thread(repository.status, city)


@app.get("/v1/locations")
async def catalog_locations(
    city: str | None = None,
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repository: CatalogRepository = Depends(get_catalog_repository),
) -> list[dict]:
    return await asyncio.to_thread(
        repository.list_locations,
        city_name=city,
        query=query,
        limit=limit,
        offset=offset,
    )


@app.get("/v1/locations/{location_id}")
async def catalog_location(
    location_id: str,
    repository: CatalogRepository = Depends(get_catalog_repository),
) -> dict:
    result = await asyncio.to_thread(
        repository.get_location, location_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return result


@app.get("/v1/locations/{location_id}/competitors")
async def catalog_competitors(
    location_id: str,
    repository: CatalogRepository = Depends(get_catalog_repository),
) -> list[dict]:
    if await asyncio.to_thread(repository.get_location, location_id) is None:
        raise HTTPException(status_code=404, detail="Location not found")
    return await asyncio.to_thread(
        repository.competitors, location_id
    )


@app.post("/reports", response_model=AgentRunResult)
async def create_report(
    request: ReportRequest,
    service: SalonReportService = Depends(get_service),
) -> AgentRunResult:
    try:
        return await service.create_report(
            request.query,
            criteria=request.criteria,
            city=request.city or get_settings().default_city,
        )
    except PlaceNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ошибка источника данных") from error
