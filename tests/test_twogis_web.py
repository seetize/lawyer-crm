from app.providers.twogis_web import TwoGisEnrichedProvider


def test_public_card_parses_prices() -> None:
    html = """
    <article>
      <button>Маникюр с покрытием</button>
      <span>от 1 200 ₽</span>
    </article>
    """

    services = TwoGisEnrichedProvider.parse_services(html)

    assert len(services) == 1
    assert services[0].name == "Маникюр с покрытием"
    assert services[0].price == "от 1 200 ₽"


def test_public_card_parses_review_text() -> None:
    review = (
        "Очень понравился салон: мастер внимательно выслушала "
        "и аккуратно выполнила стрижку."
    )
    html = f'<a href="/firm/1/tab/reviews">{review}</a>'

    reviews = TwoGisEnrichedProvider.parse_reviews(html)

    assert len(reviews) == 1
    assert reviews[0].text == review


def test_reviews_api_keeps_identity_and_official_answer() -> None:
    payload = {
        "reviews": [
            {
                "id": "42",
                "text": "Спасибо мастеру за аккуратную стрижку.",
                "rating": 5,
                "date_created": "2026-07-01T10:00:00+04:00",
                "is_hidden": False,
                "user": {"name": "Анна"},
                "official_answer": {
                    "text": "Анна, спасибо, будем рады видеть снова!",
                    "date_created": "2026-07-02T12:00:00+04:00",
                },
            }
        ]
    }

    reviews = TwoGisEnrichedProvider.parse_api_reviews(
        payload,
        "70000000000000000",
        "https://2gis.ru/firm/70000000000000000",
    )

    assert len(reviews) == 1
    assert reviews[0].provider_review_id == "42"
    assert reviews[0].author == "Анна"
    assert reviews[0].organization_replies[0].text.startswith("Анна, спасибо")
