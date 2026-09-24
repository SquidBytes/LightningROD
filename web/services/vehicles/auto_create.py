"""Create the vehicle row a source first reports on, and activate it if none is."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle import EVVehicle
from web.queries.settings import get_app_setting, set_app_setting

logger = logging.getLogger("lightningrod.vehicles")


async def ensure_vehicle(
    db: AsyncSession,
    device_id: str,
    *,
    source_system: str,
    vin: str | None = None,
    display_name: str | None = None,
) -> None:
    exists = await db.scalar(
        select(EVVehicle.id).where(EVVehicle.device_id == device_id).limit(1)
    )
    if exists is not None:
        return
    vehicle = EVVehicle(
        display_name=display_name or device_id,
        device_id=device_id,
        vin=vin,
        source_system=source_system,
    )
    db.add(vehicle)
    try:
        await db.flush()
    except IntegrityError:
        # Another writer created it first.
        await db.rollback()
        return
    logger.info("Auto-created vehicle record for device_id=%s", device_id)
    if not await get_app_setting(db, "active_vehicle_id", ""):
        await set_app_setting(db, "active_vehicle_id", str(vehicle.id))
        logger.info("Auto-activated vehicle %s (id=%d)", device_id, vehicle.id)
