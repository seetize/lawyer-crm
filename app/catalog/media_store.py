from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.models import SalonProfile


class MediaStore:
    def __init__(self, root: str | Path = "data/media", concurrency: int = 4) -> None:
        self.root = Path(root)
        self.concurrency = max(1, min(concurrency, 8))

    async def download_profile(self, profile: SalonProfile) -> int:
        jobs: list[tuple[str, object, str]] = []
        jobs.extend((item.url, item, "local_path") for item in profile.media)
        for story in profile.stories:
            jobs.extend((url, story, "local_media_paths") for url in story.media_urls)
        for news in profile.news:
            jobs.extend((str(url), news, "local_photo_paths") for url in news.photos)
        semaphore = asyncio.Semaphore(self.concurrency)
        directory = self.root / profile.primary_provider / profile.provider_id
        directory.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            async def download(url: str, target: object, field: str) -> bool:
                async with semaphore:
                    path = await self._download(client, url, directory)
                if path is None:
                    return False
                relative = path.as_posix()
                if field == "local_path":
                    setattr(target, field, relative)
                else:
                    values = getattr(target, field)
                    if relative not in values:
                        values.append(relative)
                return True

            return sum(await asyncio.gather(*(download(*job) for job in jobs)))

    async def _download(
        self, client: httpx.AsyncClient, url: str, directory: Path
    ) -> Path | None:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if not content_type.startswith(("image/", "video/")):
            return None
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
        }.get(content_type, Path(urlparse(url).path).suffix or ".bin")
        path = directory / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}{suffix}"
        if path.exists() and path.stat().st_size == len(response.content):
            return path
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(response.content)
        temporary.replace(path)
        return path
