from collections.abc import Awaitable, Callable

from app.agents.collector import CollectorAgent
from app.agents.contracts import AgentRunResult, WorkflowStatus
from app.agents.publisher import PublisherAgent
from app.agents.review_analyst import ReviewAnalystAgent
from app.agents.reviewer import ReviewerAgent
from app.report import build_report


class SalonAgentWorkflow:
    def __init__(
        self,
        collector: CollectorAgent,
        reviewer: ReviewerAgent,
        review_analyst: ReviewAnalystAgent,
        publisher: PublisherAgent,
        max_attempts: int = 2,
    ) -> None:
        self.collector = collector
        self.reviewer = reviewer
        self.review_analyst = review_analyst
        self.publisher = publisher
        self.max_attempts = max_attempts

    async def run(
        self,
        query: str,
        city: str | None = None,
        criteria: list[str] | None = None,
        deliver: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult:
        profile = None
        missing_fields: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            profile = await self.collector.run(
                query, city, missing_fields, profile
            )
            review = await self.reviewer.run(profile)
            if review.is_valid:
                profile = await self.review_analyst.run(profile)
            status, report = await self.publisher.run(
                profile, review, criteria, deliver
            )
            if status == WorkflowStatus.READY:
                return AgentRunResult(
                    status=status,
                    profile=profile,
                    report=report,
                    attempts=attempt,
                )

            # Агент 3 возвращает отказ агенту 2, агент 2 формирует задачу
            # на повторный целевой сбор для агента 1.
            missing_fields = await self.reviewer.rework_request(review)

        # После повторной проверки публикуем честный частичный отчёт:
        # отсутствующие поля явно помечены, а найденные данные не теряются.
        profile = await self.review_analyst.run(profile)
        report = build_report(profile, criteria)
        if deliver:
            await deliver(report)
        return AgentRunResult(
            status=WorkflowStatus.NEEDS_REWORK,
            profile=profile,
            report=report,
            missing_fields=missing_fields,
            attempts=self.max_attempts,
        )

    async def run_profile(
        self,
        profile,
        criteria: list[str] | None = None,
        deliver: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult:
        """Validate and publish a profile selected by an exact provider ID."""
        review = await self.reviewer.run(profile)
        profile = await self.review_analyst.run(profile)
        if review.is_valid:
            status, report = await self.publisher.run(
                profile, review, criteria, deliver
            )
        else:
            status = WorkflowStatus.NEEDS_REWORK
            report = build_report(profile, criteria)
            if deliver:
                await deliver(report)
        return AgentRunResult(
            status=status,
            profile=profile,
            report=report,
            missing_fields=review.missing_fields,
            attempts=1,
        )
