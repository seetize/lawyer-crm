from app.providers.twogis import TwoGisPlaceProvider


def test_twogis_closed_branch_is_inactive() -> None:
    assert TwoGisPlaceProvider.is_active({"status": "open"})
    assert not TwoGisPlaceProvider.is_active(
        {"flags": {"temporarily_closed": True}}
    )


def test_twogis_response_is_normalized() -> None:
    profile = TwoGisPlaceProvider._normalize(
        {
            "id": "70000001035054115",
            "name": "Париж, салон красоты",
            "address_name": "Боевая улица, 25",
            "reviews": {
                "general_rating": 4.8,
                "general_review_count_with_stars": 359,
            },
            "schedule": {
                "Mon": {"working_hours": [{"from": "10:15", "to": "22:00"}]}
            },
        },
        "Париж",
        "Астрахань",
    )

    assert profile.provider == "2gis"
    assert profile.rating == 4.8
    assert profile.reviews_count == 359
    assert profile.address == "Боевая улица, 25, Астрахань"
    assert profile.opening_hours == ["Пн: 10:15–22:00"]
