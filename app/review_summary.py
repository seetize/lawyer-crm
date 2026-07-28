import logging
from collections import Counter
from typing import Protocol

import httpx

from app.models import Review


class ReviewSummarizer(Protocol):
    async def summarize(self, place_name: str, reviews: list[Review]) -> str | None:
        """Return a grounded Russian-language summary or None."""


class LocalReviewSummarizer:
    """Free deterministic review analysis that runs without network access."""

    positive_markers = (
        "отлич",
        "хорош",
        "понрав",
        "прекрас",
        "вежлив",
        "аккурат",
        "чист",
        "быстр",
        "качеств",
        "уют",
        "рекоменд",
        "доволь",
    )
    negative_markers = (
        "плох",
        "ужас",
        "не понрав",
        "груб",
        "долго",
        "ждал",
        "ждала",
        "ожидан",
        "дорог",
        "гряз",
        "испорт",
        "опозд",
        "невнимат",
        "разочар",
    )
    themes = (
        (
            "качество результата",
            "качество результата",
            (
            "результат",
            "качеств",
            "стриж",
            "маник",
            "ремонт",
            "кофе",
            "еда",
            ),
        ),
        (
            "профессионализм специалистов",
            "работу отдельных специалистов",
            ("мастер", "специалист", "механик", "врач", "барбер"),
        ),
        (
            "отношение персонала",
            "отношение персонала",
            ("персонал", "администратор", "вежлив", "груб", "отношение"),
        ),
        (
            "чистоту и приятную обстановку",
            "чистоту или обстановку",
            ("чист", "гряз", "уют", "атмосфер", "интерьер"),
        ),
        (
            "скорость обслуживания",
            "ожидание и пунктуальность",
            ("быстр", "долго", "ждал", "ожидан", "опозд", "время"),
        ),
        ("соотношение цены и качества", "цены", ("цен", "дорог", "дешев", "стоим")),
        (
            "удобство записи",
            "запись и организацию",
            ("запис", "администратор", "организац", "очеред"),
        ),
    )

    def __init__(self, max_chars: int = 700) -> None:
        self.max_chars = max_chars

    async def summarize(self, place_name: str, reviews: list[Review]) -> str | None:
        if not reviews:
            return None

        positive_themes: Counter[str] = Counter()
        negative_themes: Counter[str] = Counter()
        positive_reviews = 0
        negative_reviews = 0
        replied_reviews = 0

        for review in reviews:
            text = " ".join(review.text.casefold().split())
            sentiment = self._sentiment(text, review.rating)
            if sentiment > 0:
                positive_reviews += 1
            elif sentiment < 0:
                negative_reviews += 1
            if review.organization_replies:
                replied_reviews += 1
            for positive_theme, negative_theme, markers in self.themes:
                if not any(marker in text for marker in markers):
                    continue
                if sentiment > 0:
                    positive_themes[positive_theme] += 1
                elif sentiment < 0:
                    negative_themes[negative_theme] += 1

        overall = self._overall(positive_reviews, negative_reviews)
        praised = self._render_themes(
            positive_themes,
            "явных повторяющихся положительных тем не найдено",
        )
        criticized = self._render_themes(
            negative_themes,
            "выраженных повторяющихся претензий не найдено",
        )
        attention = (
            self._render_themes(negative_themes, "")
            if negative_themes
            else "существенных повторяющихся рисков в доступных отзывах не обнаружено"
        )
        summary = (
            f"По {len(reviews)} доступным отзывам впечатление {overall}.\n"
            f"Чаще хвалят: {praised}.\n"
            f"Чаще критикуют: {criticized}.\n"
            f"На что обратить внимание: {attention}. "
            f"Ответы организации: опубликованы к {replied_reviews} из "
            f"{len(reviews)} собранных отзывов. "
            "Вывод отражает только опубликованные отзывы и может не описывать "
            "весь клиентский опыт."
        )
        if len(summary) <= self.max_chars:
            return summary
        return summary[: self.max_chars - 1].rstrip(" ,.;") + "…"

    def _sentiment(self, text: str, rating: float | None) -> int:
        if rating is not None:
            if rating >= 4:
                return 1
            if rating <= 2:
                return -1
        positive = sum(marker in text for marker in self.positive_markers)
        negative = sum(marker in text for marker in self.negative_markers)
        return (positive > negative) - (negative > positive)

    @staticmethod
    def _overall(positive: int, negative: int) -> str:
        if positive > negative * 2:
            return "преимущественно положительное"
        if negative > positive * 2:
            return "преимущественно отрицательное"
        if positive or negative:
            return "смешанное"
        return "нейтральное: в текстах мало явных оценочных формулировок"

    @staticmethod
    def _render_themes(themes: Counter[str], empty: str) -> str:
        return ", ".join(theme for theme, _ in themes.most_common(3)) or empty


class OpenAIReviewSummarizer:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.6-luna",
        max_chars: int = 700,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_chars = max_chars

    async def summarize(self, place_name: str, reviews: list[Review]) -> str | None:
        if not reviews:
            return None

        input_text = self._review_input(place_name, reviews)
        payload = {
            "model": self.model,
            "instructions": (
                "Ты аналитик отзывов о заведениях. Создай объективную выжимку "
                "на русском языке только из предоставленных отзывов. Не выдумывай "
                "факты, услуги, причины или статистику. Отделяй единичное мнение "
                "от повторяющихся наблюдений. Если отрицательных или положительных "
                "наблюдений нет, скажи об этом прямо. Формат: короткий общий вывод; "
                "затем строки «Чаще хвалят:», «Чаще критикуют:» и "
                "«На что обратить внимание:». "
                f"Весь ответ — от 400 до {self.max_chars} символов."
            ),
            "input": input_text,
            "reasoning": {"effort": "none"},
            "text": {"verbosity": "low"},
            "max_output_tokens": 350,
            "store": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                response = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
            summary = self._output_text(response.json())
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            logging.exception("Review summarization failed")
            return None

        if not summary:
            return None
        return summary[: self.max_chars].rstrip()

    @staticmethod
    def _review_input(place_name: str, reviews: list[Review]) -> str:
        rendered = [f"Заведение: {place_name}", "Отзывы:"]
        for index, review in enumerate(reviews, start=1):
            text = " ".join(review.text.split())[:1200]
            rating = f" Оценка: {review.rating:g}/5." if review.rating else ""
            rendered.append(f"{index}.{rating} {text}")
            for reply in review.organization_replies:
                reply_text = " ".join(reply.text.split())[:1200]
                rendered.append(f"Ответ организации на отзыв {index}: {reply_text}")
        return "\n".join(rendered)

    @staticmethod
    def _output_text(data: dict) -> str | None:
        parts: list[str] = []
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    parts.append(content["text"].strip())
        return "\n".join(parts).strip() or None


def build_review_summarizer(settings: object) -> ReviewSummarizer | None:
    provider = getattr(settings, "review_summary_provider", "local")
    max_chars = getattr(settings, "review_summary_max_chars", 700)
    if provider == "local":
        return LocalReviewSummarizer(max_chars=max_chars)
    if provider == "off":
        return None

    api_key = getattr(settings, "openai_api_key", None)
    enabled = getattr(settings, "openai_reviews_enabled", True)
    if not api_key or not enabled:
        return None
    return OpenAIReviewSummarizer(
        api_key=api_key,
        model=getattr(settings, "openai_review_model", "gpt-5.6-luna"),
        max_chars=max_chars,
    )
