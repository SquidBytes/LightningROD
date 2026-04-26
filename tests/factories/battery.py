"""Factory for EVBatteryStatus model instances."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from tests.factories import BaseFactory


class BatteryStatusFactory(BaseFactory):
    """Create EVBatteryStatus instances with realistic battery telemetry."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVBatteryStatus:
        cls._next_id()
        defaults = {
            "device_id": "TEST_VIN_001",
            "hv_battery_soc": cls._random_float(10.0, 100.0, 1),
            "hv_battery_kw": cls._random_float(0.0, 150.0),
            "hv_battery_voltage": cls._random_float(350.0, 420.0),
            "hv_battery_temperature": cls._random_float(15.0, 35.0),
            "recorded_at": cls._random_datetime(days_back=7),
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        status = EVBatteryStatus(**defaults)
        db.add(status)
        await db.flush()
        return status
