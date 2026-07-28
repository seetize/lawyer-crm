import json

from app.providers.yandex import YandexMapsProvider


def state_html(payload: dict) -> str:
    return (
        '<html><script class="state-view" type="application/json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script></html>"
    )


def test_yandex_search_card_is_normalized_as_primary_source() -> None:
    payload = {
        "search": {
            "results": [
                {
                    "id": "1732432809",
                    "title": "GSNV-Lab",
                    "fullAddress": "Астрахань, улица 28-й Армии, 14",
                    "ratingData": {
                        "ratingValue": 4.9,
                        "reviewCount": 151,
                    },
                    "categories": [{"name": "Парикмахерская"}],
                    "businessLinks": [
                        {
                            "type": "booking",
                            "href": "https://n575263.yclients.com/",
                        }
                    ],
                    "workingTime": [
                        [
                            {
                                "from": {"hours": 10, "minutes": 0},
                                "to": {"hours": 20, "minutes": 0},
                            }
                        ]
                    ]
                    * 7,
                }
            ]
        }
    }

    organizations = YandexMapsProvider.parse_organizations(state_html(payload))
    profile = YandexMapsProvider.normalize_organization(organizations[0])

    assert profile.primary_provider == "yandex_maps"
    assert profile.provider_id == "1732432809"
    assert profile.rating == 4.9
    assert profile.reviews_count == 151
    assert str(profile.booking_url) == "https://n575263.yclients.com/"
    assert len(profile.opening_hours) == 7


def test_yandex_review_page_keeps_company_reply() -> None:
    payload = {
        "reviewsState": {
            "reviews": [
                {
                    "reviewId": "review-1",
                    "text": "Очень хороший мастер.",
                    "rating": 5,
                    "updatedTime": "2026-07-01",
                    "author": {"name": "Анна"},
                    "businessComment": {
                        "text": "Спасибо за ваш отзыв!",
                        "updatedTime": "2026-07-02",
                    },
                }
            ],
            "params": {"page": 1, "totalPages": 4, "count": 151},
        }
    }

    parsed = YandexMapsProvider.parse_review_page(
        state_html(payload),
        "1732432809",
    )

    assert parsed is not None
    reviews, total_pages = parsed
    assert total_pages == 4
    assert reviews[0].provider == "yandex_maps"
    assert reviews[0].provider_review_id == "review-1"
    assert reviews[0].organization_replies[0].text == "Спасибо за ваш отзыв!"


def test_yandex_prices_are_parsed() -> None:
    payload = {
        "topObjects": {
            "categories": [
                {
                    "categoryItems": [
                        {
                            "title": "Мужская стрижка",
                            "description": "60 минут",
                            "price": 1000,
                            "currency": "₽",
                        }
                    ]
                }
            ]
        }
    }

    services = YandexMapsProvider.parse_prices(
        state_html(payload),
        "https://yandex.ru/maps/org/1/prices/",
    )

    assert len(services) == 1
    assert services[0].name == "Мужская стрижка"
    assert services[0].price == "1000 ₽"
    assert services[0].provider == "yandex_maps"


def test_yandex_web_api_signature_is_stable() -> None:
    signature = YandexMapsProvider._query_signature(
        {
            "ajax": "1",
            "businessId": "1",
            "csrfToken": "abc:123",
            "locale": "ru_RU",
            "page": "1",
            "pageSize": "50",
            "ranking": "by_relevance_org",
        }
    )

    assert signature == "3975830889"
