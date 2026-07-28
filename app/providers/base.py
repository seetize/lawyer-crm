from abc import ABC, abstractmethod

from app.models import SalonProfile


class PlaceNotFoundError(Exception):
    pass


class PlaceProvider(ABC):
    @abstractmethod
    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        """Find one establishment and return normalized information."""


class ProfileEnricher(ABC):
    @abstractmethod
    async def enrich(self, profile: SalonProfile) -> SalonProfile:
        """Add source-specific data to an already identified establishment."""
