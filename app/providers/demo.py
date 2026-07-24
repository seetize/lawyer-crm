from app.models import Review, SalonProfile, Service, SourceRef
from app.providers.base import PlaceProvider


class DemoPlaceProvider(PlaceProvider):
    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        city = city or "Астрахань"
        return SalonProfile(
            provider="demo",
            provider_id="demo-salon-1",
            name=query.strip(),
            address=f"Тестовая улица, 10, {city}",
            rating=4.7,
            reviews_count=128,
            reviews=[
                Review(
                    author="Анна",
                    rating=5,
                    text="Вежливые мастера, чисто и аккуратно.",
                    published_at="месяц назад",
                ),
                Review(
                    author="Лейла",
                    rating=4,
                    text="Хороший результат, но пришлось немного подождать.",
                    published_at="2 месяца назад",
                ),
            ],
            price_level="средний",
            opening_hours=["Пн–Вс: 10:00–21:00"],
            website="https://example.com",
            map_url="https://maps.google.com",
            services=[
                Service(name="Стрижка", price="от 30 AZN", duration="60 мин"),
                Service(name="Маникюр", price="от 25 AZN", duration="90 мин"),
            ],
            masters=["Алина — стилист", "Мария — nail-мастер"],
            available_slots=["Сегодня 18:30", "Завтра 11:00", "Завтра 15:30"],
            sources=[SourceRef(provider="demo", url="https://example.com")],
        )
