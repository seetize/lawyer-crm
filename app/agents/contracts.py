from enum import StrEnum

from pydantic import BaseModel, Field

from app.models import SalonProfile


class WorkflowStatus(StrEnum):
    READY = "ready"
    NEEDS_REWORK = "needs_rework"


class ReviewResult(BaseModel):
    status: WorkflowStatus
    missing_fields: list[str] = Field(default_factory=list)
    feedback: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.status == WorkflowStatus.READY


class AgentRunResult(BaseModel):
    status: WorkflowStatus
    profile: SalonProfile
    report: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    attempts: int

