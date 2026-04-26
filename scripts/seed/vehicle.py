"""Seed module: sample F-150 Lightning vehicle (active)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle import EVVehicle

_DEMO_VIN = "1FT6W1EV0NWG00000"


async def seed(db: AsyncSession) -> int:
    """Insert one active F-150 Lightning demo vehicle. Returns rows inserted."""
    existing = await db.execute(select(EVVehicle).where(EVVehicle.vin == _DEMO_VIN))
    if existing.scalar_one_or_none() is not None:
        return 0

    vehicle = EVVehicle(
        vin=_DEMO_VIN,
        device_id="DEMO_F150_LIGHTNING_001",
        display_name="Demo F-150 Lightning",
        make="Ford",
        model="F-150 Lightning",
        year=2024,
        trim_level="XLT",
        battery_option="Standard Range",
        battery_capacity_kwh=98.0,
        battery_gross_capacity_kwh=108.0,
        source_system="seed",
    )
    db.add(vehicle)
    await db.flush()
    return 1
