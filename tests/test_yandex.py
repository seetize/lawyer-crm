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
                    "awards": {"goodPlaceYear": "2026"},
                    "staff": [
                        {"name": "Новый мастер", "active": True},
                        {"name": "Старый мастер", "active": False},
                    ],
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
    assert profile.categories == ["Парикмахерская"]
    assert profile.awards == ["Хорошее место 2026"]
    assert profile.masters == ["Новый мастер"]
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
                    "categoryName": "Стрижки",
                    "categoryItems": [
                        {
                            "sourceId": "service-1",
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
    assert services[0].category == "Стрижки"
    assert services[0].provider_service_id == "service-1"


def test_yandex_news_payload_is_normalized() -> None:
    news = YandexMapsProvider.parse_news_payload(
        {
            "data": {
                "items": [
                    {
                        "id": 42,
                        "uri": "tycoon_events/1.x/post/42",
                        "text": " Новая   услуга ",
                        "publicationTime": 1_700_138_772_654,
                        "photos": [{"urlTemplate": "https://example.test/%s.jpg"}],
                    }
                ]
            }
        },
        "1732432809",
        "gsnv_lab",
    )

    assert len(news) == 1
    assert news[0].text == "Новая услуга"
    assert news[0].published_at == "2023-11-16"
    assert str(news[0].photos[0]) == "https://example.test/XL.jpg"


def test_yandex_card_normalizes_features_stories_and_branches() -> None:
    item = {
        "featureGroups": [{"name": "Удобства", "features": [{"name": "Wi-Fi", "valueName": "да"}]}],
        "businessProperties": {"has_verified_owner": False, "geoproduct_poi_color": "#fff"},
        "stories": [{"id": "s1", "title": "Новинка", "category": "Услуги", "imageUrl": "https://example.test/story.jpg"}],
        "branches": [{"id": "42", "title": "Филиал", "address": "ул. Кирова, 1", "coordinates": [39.8, 57.6]}],
    }

    assert YandexMapsProvider._features(item)[0].name == "Wi-Fi"
    assert len(YandexMapsProvider._features(item)) == 1
    assert YandexMapsProvider._stories(item)[0].category == "Услуги"
    assert YandexMapsProvider._branches(item)[0].provider_id == "42"

    html = '<script type="application/json">{"widgets":{"storyGroups":[{"categoryName":"Услуги","items":[{"storyId":"nested","title":"Акция","media":{"imageUrl":"https://example.test/a.jpg"}}]}]}}</script>'
    stories = YandexMapsProvider.parse_stories_html(html)
    assert stories[0].provider_story_id == "nested"
    assert stories[0].category == "Услуги"


def test_ranking_scope_uses_city_and_metro_rules() -> None:
    small = YandexMapsProvider._ranking_scope(
        {
            "coordinates": [48.03, 46.34],
            "region": {
                "names": {"nominative": "Астрахань"},
                "bounds": [[47.8, 46.2], [48.2, 46.5]],
                "zoom": 12,
            },
        },
        None,
    )
    metro = YandexMapsProvider._ranking_scope(
        {
            "coordinates": [37.62, 55.75],
            "region": {"names": {"nominative": "Москва"}, "zoom": 9},
            "metro": [
                {
                    "name": "Тверская",
                    "distanceValue": 300,
                    "coordinates": [37.605, 55.765],
                }
            ],
        },
        None,
    )

    assert small[0:2] == ("Астрахань", "city")
    assert metro[0:2] == ("метро Тверская", "metro")
    assert metro[2] == [37.605, 55.765]


def test_yandex_profile_keeps_unique_metro_stations() -> None:
    assert YandexMapsProvider._metro_stations(
        {
            "metro": [
                {"name": "Тверская"},
                {"name": "Тверская"},
                {"name": "Пушкинская"},
            ]
        }
    ) == ["Тверская", "Пушкинская"]


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

    cyrillic_signature = YandexMapsProvider._query_signature(
        {"ajax": "1", "csrfToken": "abc:123", "text": "салон красоты"}
    )
    assert cyrillic_signature == "2296987397"


def test_real_yandex_feature_shape_and_media_are_normalized() -> None:
    item = {
        "features": [
            {"id": "wifi", "name": "Wi-Fi", "value": True},
            {
                "id": "access",
                "name": "Доступность",
                "value": [{"id": "full", "name": "доступно"}],
            },
        ],
        "featureGroups": [{"name": "Удобства", "featureIds": ["wifi", "access"]}],
        "photos": {
            "items": [
                {"id": "p1", "urlTemplate": "https://example.test/%s/photo.jpg"}
            ]
        },
    }

    features = YandexMapsProvider._features(item)
    media = YandexMapsProvider._media(item)

    assert [(value.name, value.value) for value in features] == [
        ("Wi-Fi", "True"),
        ("Доступность", "доступно"),
    ]
    assert media[0].url == "https://example.test/orig/photo.jpg"


def test_story_screens_do_not_become_duplicate_stories() -> None:
    item = {
        "lastMileStory": {
            "storyId": "story-1",
            "title": "Как пройти",
            "tags": ["lastMile"],
            "screens": [
                {"id": "screen-1", "image": {"urlTemplate": "https://example.test/1.jpg"}},
                {"id": "screen-2", "image": {"urlTemplate": "https://example.test/2.jpg"}},
            ],
        }
    }

    stories = YandexMapsProvider._stories(item)

    assert len(stories) == 1
    assert stories[0].media_urls == [
        "https://example.test/1.jpg",
        "https://example.test/2.jpg",
    ]
