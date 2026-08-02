import asyncio

from app.async_runtime import configure_asyncio_policy
from app.config import Settings
from app.providers import build_provider


async def main() -> None:
    settings = Settings()
    profile = await build_provider(settings).collect("GSNV-Lab", settings.default_city)
    assert profile.name
    assert profile.primary_provider == "yandex_maps"
    assert profile.rating is not None
    assert profile.reviews
    print(
        "live_smoke_ok",
        profile.name,
        len(profile.reviews),
        len(profile.services),
    )


if __name__ == "__main__":
    configure_asyncio_policy()
    asyncio.run(main())
