"""Factory for EVTripMetrics model instances."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from tests.factories import BaseFactory


class TripFactory(BaseFactory):
    """Create EVTripMetrics instances with realistic trip data."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVTripMetrics:
        cls._next_id()
        distance = cls._random_float(5.0, 100.0)
        duration = cls._random_float(10.0, 120.0)
        energy = cls._random_float(2.0, 30.0)
        # Efficiency in kWh/100km
        efficiency = round((energy / distance) * 100, 2) if distance > 0 else 0.0
        end_time = cls._random_datetime(days_back=14)

        defaults = {
            "device_id": "TEST_VIN_001",
            "distance": distance,
            "duration": duration,
            "energy_consumed": energy,
            "efficiency": efficiency,
            "end_time": end_time,
            "is_complete": True,
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        trip = EVTripMetrics(**defaults)
        db.add(trip)
        await db.flush()
        return trip
