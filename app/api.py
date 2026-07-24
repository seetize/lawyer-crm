from fastapi import Depends, FastAPI, HTTPException

from app.config import Settings, get_settings
from app.agents.contracts import AgentRunResult
from app.models import ReportRequest
from app.providers import build_provider
from app.providers.base import PlaceNotFoundError
from app.service import SalonReportService

app = FastAPI(title="Beauty Inspector API", version="0.1.0")


def get_service(settings: Settings = Depends(get_settings)) -> SalonReportService:
    return SalonReportService(build_provider(settings))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
