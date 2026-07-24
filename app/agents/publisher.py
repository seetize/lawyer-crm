from collections.abc import Awaitable, Callable

from app.agents.contracts import ReviewResult, WorkflowStatus
from app.models import SalonProfile
from app.report import build_report


class PublisherAgent:
    """Агент 3: выпускает только отчёт, прошедший проверку."""

    async def run(
        self,
        profile: SalonProfile,
        review: ReviewResult,
        criteria: list[str] | None = None,
        deliver: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[WorkflowStatus, str | None]:
        if not review.is_valid:
            return WorkflowStatus.NEEDS_REWORK, None
        report = build_report(profile, criteria)
        if deliver:
            await deliver(report)
        return WorkflowStatus.READY, report
