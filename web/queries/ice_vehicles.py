"""ICE vehicle registry queries and default-vehicle helpers.

Mirrors web/queries/vehicles.py CRUD shape. The is_default flag is column-level
(no app_settings indirection) — guarded by partial unique index
uq_ice_vehicles_one_default + set_default_ice_vehicle helper for the demote-others
invariant.
"""

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.ice_vehicle import IceVehicle


async def get_all_ice_vehicles(db: AsyncSession) -> list[IceVehicle]:
    """Return all ICE vehicles ordered by label."""
    result = await db.execute(select(IceVehicle).order_by(IceVehicle.label))
    return list(result.scalars().all())


async def get_ice_vehicle_by_id(db: AsyncSession, ice_id: int) -> IceVehicle | None:
    """Return a single ICE vehicle by ID, or None if not found."""
    result = await db.execute(select(IceVehicle).where(IceVehicle.id == ice_id))
    return result.scalar_one_or_none()


async def get_default_ice_vehicle(db: AsyncSession) -> IceVehicle | None:
    """Return the row with is_default=True, or None when no default set."""
    result = await db.execute(select(IceVehicle).where(IceVehicle.is_default.is_(True)))
    return result.scalar_one_or_none()


async def set_default_ice_vehicle(db: AsyncSession, ice_id: int) -> bool:
    """Promote one row to default; demote all others. Single-default invariant."""
    row = await get_ice_vehicle_by_id(db, ice_id)
    if row is None:
        return False
    await db.execute(update(IceVehicle).values(is_default=False))
    row.is_default = True
    await db.commit()
    return True


async def create_ice_vehicle(
    db: AsyncSession,
    label: str,
    fuel_efficiency_l_per_100km: float,
    tank_capacity_l: float | None = None,
    is_default: bool = False,
) -> IceVehicle | None:
    """Create an ICE vehicle row. If is_default=True, demote all other rows first.

    Returns the new row, or None on IntegrityError (e.g. partial unique index conflict).
    """
    if is_default:
        await db.execute(update(IceVehicle).values(is_default=False))
    row = IceVehicle(
        label=label,
        fuel_efficiency_l_per_100km=fuel_efficiency_l_per_100km,
        tank_capacity_l=tank_capacity_l,
        is_default=is_default,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    await db.commit()
    await db.refresh(row)
    return row


async def update_ice_vehicle(
    db: AsyncSession,
    ice_id: int,
    **kwargs,
) -> IceVehicle | None:
    """Update whitelisted fields on an ICE vehicle row.

    Allowed fields: label, fuel_efficiency_l_per_100km, tank_capacity_l, is_default.
    Setting is_default=True demotes all other rows first.
    """
    row = await get_ice_vehicle_by_id(db, ice_id)
    if row is None:
        return None
    allowed_fields = {
        "label",
        "fuel_efficiency_l_per_100km",
        "tank_capacity_l",
        "is_default",
    }
    if kwargs.get("is_default") is True:
        await db.execute(
            update(IceVehicle).where(IceVehicle.id != ice_id).values(is_default=False)
        )
    for key, value in kwargs.items():
        if key in allowed_fields:
            setattr(row, key, value)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    await db.commit()
    await db.refresh(row)
    return row


async def delete_ice_vehicle(db: AsyncSession, ice_id: int) -> bool:
    """Delete an ICE vehicle row.

    Refuses to delete the currently-default row when other rows exist (RESEARCH OQ4).
    The user must promote a different row to default first. Returns False on guard
    failure or row-not-found; True on successful delete.
    """
    row = await get_ice_vehicle_by_id(db, ice_id)
    if row is None:
        return False
    if row.is_default:
        # Allow the delete only when this is the LAST row (no other defaults to promote).
        other_count = await db.execute(
            select(IceVehicle).where(IceVehicle.id != ice_id).limit(1)
        )
        if other_count.scalar_one_or_none() is not None:
            return False
    await db.delete(row)
    await db.commit()
    return True
