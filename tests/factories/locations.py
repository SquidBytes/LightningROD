"""Factories for EVLocationLookup and EVLocation model instances."""


from sqlalchemy.ext.asyncio import AsyncSession

from db.models.location import EVLocation
from db.models.reference import EVLocationLookup
from tests.factories import BaseFactory


class LocationLookupFactory(BaseFactory):
    """Create EVLocationLookup instances (named charging stations)."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVLocationLookup:
        n = cls._next_id()
        defaults = {
            "location_name": f"Test Station {n}",
            "address": f"{n * 100} Test St",
            "latitude": cls._random_float(45.45, 45.55, 6),
            "longitude": cls._random_float(-122.70, -122.55, 6),
            "location_type": "public",
            "network_id": None,
            "is_verified": True,
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        location = EVLocationLookup(**defaults)
        db.add(location)
        await db.flush()
        return location


class LocationFactory(BaseFactory):
    """Create EVLocation instances (GPS snapshots)."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVLocation:
        cls._next_id()
        defaults = {
            "device_id": "TEST_VIN_001",
            "latitude": cls._random_float(45.45, 45.55, 6),
            "longitude": cls._random_float(-122.70, -122.55, 6),
            "recorded_at": cls._random_datetime(days_back=7),
            "source_system": "ha_fordpass",
        }
        defaults.update(overrides)
        location = EVLocation(**defaults)
        db.add(location)
        await db.flush()
        return location
