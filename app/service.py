from collections.abc import Awaitable, Callable

from app.agents import CollectorAgent, PublisherAgent, ReviewerAgent
from app.agents.contracts import AgentRunResult
from app.agents.workflow import SalonAgentWorkflow
from app.providers.base import PlaceProvider


class SalonReportService:
    def __init__(self, provider: PlaceProvider, max_attempts: int = 2) -> None:
        self.workflow = SalonAgentWorkflow(
            collector=CollectorAgent(provider),
            reviewer=ReviewerAgent(),
            publisher=PublisherAgent(),
            max_attempts=max_attempts,
        )

    async def create_report(
        self,
        query: str,
        criteria: list[str] | None = None,
        city: str | None = None,
        deliver: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult:
        return await self.workflow.run(query, city, criteria, deliver)
