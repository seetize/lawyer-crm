import pytest

from app.models import Review
from app.providers.demo import DemoPlaceProvider
from app.review_summary import LocalReviewSummarizer, OpenAIReviewSummarizer
from app.service import SalonReportService


class FakeReviewSummarizer:
    async def summarize(self, place_name: str, reviews: list[Review]) -> str:
        assert place_name == "Beauty House"
        assert len(reviews) == 2
        return (
            "В целом впечатление положительное. "
            "Чаще хвалят: аккуратность и вежливость. "
            "Чаще критикуют: ожидание. "
            "На что обратить внимание: возможна задержка записи."
        )


@pytest.mark.asyncio
async def test_report_uses_review_synthesis_instead_of_raw_reviews() -> None:
    result = await SalonReportService(
        DemoPlaceProvider(),
        review_summarizer=FakeReviewSummarizer(),
    ).create_report("Beauty House")

    assert result.profile.reviews_summary
    assert "Выжимка из 2 доступных отзывов" in result.report
    assert "Чаще хвалят: аккуратность и вежливость" in result.report
    assert "Анна" not in result.report


def test_openai_response_text_is_extracted() -> None:
    data = {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Краткая выжимка."}
                ],
            }
        ]
    }

    assert OpenAIReviewSummarizer._output_text(data) == "Краткая выжимка."


@pytest.mark.asyncio
async def test_local_summarizer_finds_repeated_strengths_and_weaknesses() -> None:
    reviews = [
        Review(text="Отличный мастер, всё аккуратно и качественно.", author="1"),
        Review(text="Хороший результат и вежливый мастер.", author="2"),
        Review(text="Результат хороший, но пришлось долго ждать.", author="3"),
    ]

    summary = await LocalReviewSummarizer().summarize("Salon", reviews)

    assert summary is not None
    assert "Чаще хвалят:" in summary
    assert "качество результата" in summary
    assert "Чаще критикуют:" in summary
