"""Factory for EVChargingSession model instances."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from tests.factories import BaseFactory


class ChargingSessionFactory(BaseFactory):
    """Create EVChargingSession instances with realistic charging data."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVChargingSession:
        cls._next_id()
        energy = cls._random_float(5.0, 80.0)
        charge_type = cls._random_choice(["AC Level 2", "DC Fast"])
        rate = 0.15 if charge_type == "AC Level 2" else cls._random_float(0.30, 0.55)

        defaults = {
            "session_id": uuid.uuid4(),
            "device_id": "TEST_VIN_001",
            "charge_type": charge_type,
            "energy_kwh": energy,
            "start_soc": cls._random_float(10.0, 50.0, 1),
            "end_soc": cls._random_float(60.0, 100.0, 1),
            "cost": round(energy * rate, 2),
            "session_start_utc": cls._random_datetime(days_back=30),
            "is_complete": True,
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        session = EVChargingSession(**defaults)
        db.add(session)
        await db.flush()
        return session
