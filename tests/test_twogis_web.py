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
