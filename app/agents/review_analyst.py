from app.models import SalonProfile
from app.review_summary import ReviewSummarizer


class ReviewAnalystAgent:
    """Builds a grounded synthesis from all collected public reviews."""

    def __init__(self, summarizer: ReviewSummarizer | None = None) -> None:
        self.summarizer = summarizer

    async def run(self, profile: SalonProfile) -> SalonProfile:
        if self.summarizer is None or not profile.reviews:
            return profile
        summary = await self.summarizer.summarize(profile.name, profile.reviews)
        if summary:
            profile.reviews_summary = summary
        return profile
