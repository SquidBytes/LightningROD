"""Factory for EVVehicleStatus model instances."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle_status import EVVehicleStatus
from tests.factories import BaseFactory


class VehicleStatusFactory(BaseFactory):
    """Create EVVehicleStatus instances with realistic vehicle telemetry."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVVehicleStatus:
        cls._next_id()
        defaults = {
            "device_id": "TEST_VIN_001",
            "speed": cls._random_float(0.0, 80.0, 1),
            "acceleration": cls._random_float(-2.0, 2.0, 2),
            "outside_temperature": 15.0,
            "cabin_temperature": 22.0,
            "recorded_at": cls._random_datetime(days_back=7),
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        status = EVVehicleStatus(**defaults)
        db.add(status)
        await db.flush()
        return status
