from datetime import UTC, datetime

from pydantic import BaseModel, Field, HttpUrl


class Review(BaseModel):
    author: str
    rating: float | None = None
    text: str
    published_at: str | None = None


class Service(BaseModel):
    name: str
    price: str | None = None
    duration: str | None = None


class SourceRef(BaseModel):
    provider: str
    url: HttpUrl | None = None


class SalonProfile(BaseModel):
    provider: str
    provider_id: str
    name: str
    address: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    reviews: list[Review] = Field(default_factory=list)
    reviews_summary: str | None = None
    price_level: str | None = None
    opening_hours: list[str] = Field(default_factory=list)
    website: HttpUrl | None = None
    map_url: HttpUrl | None = None
    services: list[Service] = Field(default_factory=list)
    masters: list[str] = Field(default_factory=list)
    available_slots: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReportRequest(BaseModel):
    query: str = Field(min_length=2, examples=["Beauty salon, Baku"])
    city: str | None = Field(default=None, examples=["Астрахань"])
    criteria: list[str] = Field(
        default_factory=list,
        examples=[["rating", "reviews", "prices", "services", "slots", "masters"]],
    )
