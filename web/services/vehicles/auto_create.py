"""Create the vehicle row a source first reports on, and fill what it later learns.

Any source may describe the vehicle (make, model, year, battery, name). Those
facts only fill columns still empty: a value the user set is never replaced.
The display name counts as user-set unless it is still an automatic default.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle import EVVehicle
from web.queries.settings import get_app_setting, set_app_setting

logger = logging.getLogger("lightningrod.vehicles")

# Columns a source may fill when they are still empty.
FILLABLE = (
    "make",
    "model",
    "year",
    "vin",
    "trim_level",
    "battery_option",
    "battery_capacity_kwh",
    "battery_gross_capacity_kwh",
)


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def fill_vehicle(
    vehicle: EVVehicle,
    facts: dict[str, Any],
    *,
    display_name: str | None = None,
    auto_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Fill empty columns from `facts`; returns what changed."""
    changes: dict[str, Any] = {}
    for column in FILLABLE:
        value = facts.get(column)
        if not _empty(value) and _empty(getattr(vehicle, column)):
            changes[column] = value
    defaults = {vehicle.device_id, *(n for n in auto_names if n)}
    if vehicle.vin:
        defaults.add(vehicle.vin)
    current = (vehicle.display_name or "").strip()
    if display_name and display_name != current and (not current or current in defaults):
        changes["display_name"] = display_name
    for column, value in changes.items():
        setattr(vehicle, column, value)
    return changes


async def ensure_vehicle(
    db: AsyncSession,
    device_id: str,
    *,
    source_system: str,
    vin: str | None = None,
    display_name: str | None = None,
    auto_names: Iterable[str] = (),
    **facts: Any,
) -> EVVehicle | None:
    """The vehicle for `device_id`, created on first sight and filled from `facts`."""
    facts = {**facts, "vin": vin}
    auto_names = tuple(auto_names)
    vehicle = await db.scalar(
        select(EVVehicle).where(EVVehicle.device_id == device_id).limit(1)
    )
    if vehicle is not None:
        changes = fill_vehicle(vehicle, facts, display_name=display_name, auto_names=auto_names)
        if changes:
            logger.info("Filled %s on vehicle %s from %s", sorted(changes), device_id, source_system)
        return vehicle
    vehicle = EVVehicle(
        display_name=display_name or device_id,
        device_id=device_id,
        source_system=source_system,
        **{c: facts[c] for c in FILLABLE if not _empty(facts.get(c))},
    )
    db.add(vehicle)
    try:
        await db.flush()
    except IntegrityError:
        # Another writer created it first.
        await db.rollback()
        return None
    logger.info("Auto-created vehicle record for device_id=%s", device_id)
    if not await get_app_setting(db, "active_vehicle_id", ""):
        await set_app_setting(db, "active_vehicle_id", str(vehicle.id))
        logger.info("Auto-activated vehicle %s (id=%d)", device_id, vehicle.id)
    return vehicle
