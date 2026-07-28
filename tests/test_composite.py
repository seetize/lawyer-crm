from app.models import (
    OrganizationReply,
    Review,
    SalonProfile,
    SourceRating,
    SourceRef,
)
from app.providers.composite import CompositePlaceProvider


def test_merge_keeps_yandex_primary_and_combines_all_source_reviews() -> None:
    yandex = SalonProfile(
        provider="yandex_maps",
        provider_id="y1",
        primary_provider="yandex_maps",
        name="Salon",
        rating=4.9,
        reviews_count=100,
        ratings=[SourceRating(provider="yandex_maps", rating=4.9)],
        reviews=[
            Review(
                provider="yandex_maps",
                provider_review_id="r1",
                author="Анна",
                text="Хорошо",
                organization_replies=[
                    OrganizationReply(text="Спасибо", author="Организация")
                ],
            )
        ],
        sources=[SourceRef(provider="yandex_maps", provider_id="y1")],
    )
    twogis = SalonProfile(
        provider="2gis",
        provider_id="d1",
        primary_provider="2gis",
        name="Salon",
        rating=4.4,
        reviews_count=80,
        ratings=[SourceRating(provider="2gis", rating=4.4)],
        reviews=[
            Review(
                provider="2gis",
                provider_review_id="r1",
                author="Анна",
                text="Хорошо",
            )
        ],
        sources=[SourceRef(provider="2gis", provider_id="d1")],
    )

    merged = CompositePlaceProvider._merge(yandex, twogis)

    assert merged.primary_provider == "yandex_maps"
    assert merged.rating == 4.9
    assert len(merged.reviews) == 2
    assert len(merged.ratings) == 2
    assert merged.reviews[0].organization_replies[0].text == "Спасибо"
