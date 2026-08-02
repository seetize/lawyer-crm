import asyncio
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.models import SalonProfile


@dataclass
class ReportView:
    owner_user_id: int
    profile: SalonProfile
    created_at: float
    location_id: str | None = None


class MemoryReportCache:
    def __init__(
        self,
        ttl_seconds: int = 1800,
        max_items: int = 500,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.clock = clock
        self._items: dict[str, ReportView] = {}
        self._lock = asyncio.Lock()

    async def put(
        self,
        owner_user_id: int,
        profile: SalonProfile,
        location_id: str | None = None,
    ) -> str:
        async with self._lock:
            self._purge_expired()
            while len(self._items) >= self.max_items:
                oldest = min(
                    self._items,
                    key=lambda token: self._items[token].created_at,
                )
                self._items.pop(oldest, None)
            token = secrets.token_urlsafe(8)
            self._items[token] = ReportView(
                owner_user_id=owner_user_id,
                profile=profile,
                created_at=self.clock(),
                location_id=location_id,
            )
            return token

    async def get(self, token: str, owner_user_id: int) -> SalonProfile | None:
        async with self._lock:
            self._purge_expired()
            view = self._items.get(token)
            if view is None or view.owner_user_id != owner_user_id:
                return None
            return view.profile

    async def get_view(self, token: str, owner_user_id: int) -> ReportView | None:
        async with self._lock:
            self._purge_expired()
            view = self._items.get(token)
            if view is None or view.owner_user_id != owner_user_id:
                return None
            return view

    def _purge_expired(self) -> None:
        threshold = self.clock() - self.ttl_seconds
        expired = [
            token
            for token, view in self._items.items()
            if view.created_at < threshold
        ]
        for token in expired:
            self._items.pop(token, None)
