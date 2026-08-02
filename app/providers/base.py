from abc import ABC, abstractmethod

from app.models import SalonProfile


class PlaceNotFoundError(Exception):
    pass


class PlaceProvider(ABC):
    @abstractmethod
    async def collect(self, query: str, city: str | None = None) -> SalonProfile:
        """Find one establishment and return normalized information."""

    async def recollect_missing(
        self,
        query: str,
        city: str | None,
        missing_fields: list[str],
        previous: SalonProfile,
    ) -> SalonProfile | None:
        """Optionally use a different targeted strategy for missing fields.

        Returning ``None`` means that this provider has no additional strategy;
        the workflow must reuse the previous profile instead of repeating the
        same complete network collection.
        """
        return None


class ProfileEnricher(ABC):
    @abstractmethod
    async def enrich(self, profile: SalonProfile) -> SalonProfile:
        """Add source-specific data to an already identified establishment."""
