"""Factory for EVStatistics model instances (energy/aggregate records)."""

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVStatistics
from tests.factories import BaseFactory


class StatisticsFactory(BaseFactory):
    """Create EVStatistics instances for energy and aggregate data."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVStatistics:
        cls._next_id()
        defaults = {
            "total_sessions": cls._random_int(10, 200),
            "total_energy_kwh": cls._random_float(100.0, 5000.0),
            "total_cost": cls._random_float(50.0, 2000.0),
            "total_distance_added": cls._random_float(500.0, 15000.0),
            "avg_session_duration_seconds": cls._random_float(1800, 14400),
            "avg_energy_per_session_kwh": cls._random_float(15.0, 60.0),
            "avg_cost_per_kwh": cls._random_float(0.10, 0.50),
            "avg_efficiency": cls._random_float(2.5, 4.5),
        }
        defaults.update(overrides)
        stats = EVStatistics(**defaults)
        db.add(stats)
        await db.flush()
        return stats
