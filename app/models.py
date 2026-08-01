from datetime import UTC, datetime

from pydantic import BaseModel, Field, HttpUrl


class Review(BaseModel):
    author: str
    rating: float | None = None
    text: str
    published_at: str | None = None
    provider: str = "unknown"
    provider_review_id: str | None = None
    url: HttpUrl | None = None
    organization_replies: list["OrganizationReply"] = Field(default_factory=list)


class OrganizationReply(BaseModel):
    text: str
    author: str | None = None
    published_at: str | None = None


class Service(BaseModel):
    name: str
    category: str | None = None
    provider_service_id: str | None = None
    price: str | None = None
    duration: str | None = None
    provider: str | None = None
    source_url: HttpUrl | None = None


class NewsItem(BaseModel):
    provider_news_id: str
    text: str
    published_at: str | None = None
    photos: list[HttpUrl] = Field(default_factory=list)
    url: HttpUrl | None = None


class SearchRanking(BaseModel):
    query: str
    position: int | None = None
    total_results: int | None = None
    checked_results: int = 0
    scope: str
    scope_type: str
    search_url: HttpUrl | None = None


class SourceRef(BaseModel):
    provider: str
    provider_id: str | None = None
    url: HttpUrl | None = None


class SourceRating(BaseModel):
    provider: str
    rating: float | None = None
    reviews_count: int | None = None
    url: HttpUrl | None = None


class SalonProfile(BaseModel):
    provider: str
    provider_id: str
    primary_provider: str = "unknown"
    name: str
    address: str | None = None
    description: str | None = None
    categories: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    rating: float | None = None
    reviews_count: int | None = None
    ratings: list[SourceRating] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    reviews_summary: str | None = None
    price_level: str | None = None
    opening_hours: list[str] = Field(default_factory=list)
    website: HttpUrl | None = None
    map_url: HttpUrl | None = None
    services: list[Service] = Field(default_factory=list)
    masters: list[str] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    search_rankings: list[SearchRanking] = Field(default_factory=list)
    available_slots: list[str] = Field(default_factory=list)
    booking_url: HttpUrl | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReportRequest(BaseModel):
    query: str = Field(min_length=2, examples=["Beauty salon, Baku"])
    city: str | None = Field(default=None, examples=["Астрахань"])
    criteria: list[str] = Field(
        default_factory=list,
        examples=[["rating", "reviews", "prices", "services", "slots", "masters"]],
    )
