from abc import ABC, abstractmethod

from app.models import SalonProfile


class PlaceNotFoundError(Exception):
    pass


class PlaceProvider(ABC):
    @abstractmethod
    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        """Find one establishment and return normalized information."""
