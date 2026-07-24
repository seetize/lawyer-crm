from app.agents.contracts import ReviewResult, WorkflowStatus
from app.models import SalonProfile


class ReviewerAgent:
    """Агент 2: проверяет обязательные данные и формирует замечания."""

    required_labels = {
        "rating": "средняя оценка",
        "reviews": "отзывы",
        "prices": "конкретные цены",
    }

    async def run(self, profile: SalonProfile) -> ReviewResult:
        missing: list[str] = []
        if profile.rating is None:
            missing.append("rating")
        if not profile.reviews:
            missing.append("reviews")
        if not any(service.price for service in profile.services):
            missing.append("prices")

        if not missing:
            return ReviewResult(status=WorkflowStatus.READY)
        labels = ", ".join(self.required_labels[field] for field in missing)
        return ReviewResult(
            status=WorkflowStatus.NEEDS_REWORK,
            missing_fields=missing,
            feedback=f"Нужно дополнительно собрать: {labels}.",
        )

    async def rework_request(self, rejected: ReviewResult) -> list[str]:
        return rejected.missing_fields

