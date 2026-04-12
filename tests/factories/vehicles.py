"""Factory for EVVehicle model instances."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle import EVVehicle
from tests.factories import BaseFactory


class VehicleFactory(BaseFactory):
    """Create EVVehicle instances with realistic Ford Mustang Mach-E defaults."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVVehicle:
        n = cls._next_id()
        defaults = {
            "device_id": f"TEST_VIN_{n:03d}",
            "display_name": f"Test Vehicle {n}",
            "year": 2024,
            "make": "Ford",
            "model": "Mustang Mach-E",
            "trim": "Premium ER AWD",
            "battery_capacity_kwh": 91.0,          # usable
            "battery_gross_capacity_kwh": 98.8,    # gross
            "vin": f"TEST_VIN_{n:03d}",
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        vehicle = EVVehicle(**defaults)
        db.add(vehicle)
        await db.flush()
        return vehicle
