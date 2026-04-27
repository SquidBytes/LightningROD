"""Seed module: sample F-150 Lightning vehicle (active)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import AppSettings
from db.models.vehicle import EVVehicle
from db.portable_insert import portable_insert

_DEMO_VIN = "1FT6W1EV0NWG00000"


async def _set_active_vehicle(db: AsyncSession, vehicle_id: int) -> None:
    """Upsert app_settings.active_vehicle_id to point at the demo vehicle.

    Migration backfill creates a placeholder vehicle id=1; the demo vehicle
    lands at id=2. Without this upsert, every page filters by id=1 and
    renders empty. Inline upsert (not set_app_setting helper) so it stays
    inside the orchestrator's single transaction.
    """
    stmt = portable_insert(AppSettings, dialect=db.bind.dialect).values(
        key="active_vehicle_id", value=str(vehicle_id)
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": str(vehicle_id), "updated_at": func.now()},
    )
    await db.execute(stmt)


async def seed(db: AsyncSession) -> int:
    """Insert one active F-150 Lightning demo vehicle. Returns rows inserted."""
    existing = await db.execute(select(EVVehicle).where(EVVehicle.vin == _DEMO_VIN))
    existing_vehicle = existing.scalar_one_or_none()
    if existing_vehicle is not None:
        # Idempotent: still re-point active_vehicle_id in case it drifted.
        await _set_active_vehicle(db, existing_vehicle.id)
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
    await _set_active_vehicle(db, vehicle.id)
    return 1
