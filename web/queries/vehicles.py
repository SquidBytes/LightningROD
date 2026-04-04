from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle import EVVehicle
from web.queries.settings import get_app_setting, set_app_setting

# Structured vehicle presets for cascading combo-box auto-fill
# Each entry: make, model, trim, battery_kwh, year_min, year_max
VEHICLE_PRESETS = [
    # F-150 Lightning
    {"make": "Ford", "model": "F-150 Lightning", "trim": "Standard Range", "battery_kwh": 98.0, "year_min": 2022, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim": "Extended Range", "battery_kwh": 131.0, "year_min": 2022, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim": "Flash", "battery_kwh": 100.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim": "Flash", "battery_kwh": 123.0, "year_min": 2025, "year_max": 2025},

    # Mustang Mach-E
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Select SR RWD", "battery_kwh": 72.0, "year_min": 2023, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Premium SR RWD", "battery_kwh": 72.0, "year_min": 2023, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Premium ER RWD", "battery_kwh": 91.0, "year_min": 2023, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Premium ER AWD", "battery_kwh": 91.0, "year_min": 2023, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "GT", "battery_kwh": 91.0, "year_min": 2023, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Rally", "battery_kwh": 91.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Standard Range", "battery_kwh": 68.0, "year_min": 2021, "year_max": 2022},
    {"make": "Ford", "model": "Mustang Mach-E", "trim": "Extended Range", "battery_kwh": 88.0, "year_min": 2021, "year_max": 2022},

    # E-Transit
    {"make": "Ford", "model": "E-Transit", "trim": "Standard Range", "battery_kwh": 68.0, "year_min": 2022, "year_max": 2024},
    {"make": "Ford", "model": "E-Transit", "trim": "Extended Range", "battery_kwh": 89.0, "year_min": 2024, "year_max": 2025},
]

# Backward-compat alias -- old code imports BATTERY_PRESETS
# Convert to old format for any remaining references
BATTERY_PRESETS = [
    {"label": f"{p['model']} {p['trim']} ({p['year_min']}-{p['year_max']})", "kwh": p["battery_kwh"]}
    for p in VEHICLE_PRESETS
] + [{"label": "Custom", "kwh": None}]


async def get_all_vehicles(db: AsyncSession) -> list[EVVehicle]:
    """Return all vehicles ordered by display_name."""
    result = await db.execute(
        select(EVVehicle).order_by(EVVehicle.display_name)
    )
    return list(result.scalars().all())


async def get_vehicle_by_id(
    db: AsyncSession, vehicle_id: int
) -> Optional[EVVehicle]:
    """Return a single vehicle by ID, or None if not found."""
    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    return result.scalar_one_or_none()


async def create_vehicle(
    db: AsyncSession,
    display_name: str,
    make: Optional[str] = None,
    model: Optional[str] = None,
    year: Optional[int] = None,
    trim: Optional[str] = None,
    battery_capacity_kwh: Optional[float] = None,
    vin: Optional[str] = None,
    device_id: Optional[str] = None,
    source_system: Optional[str] = None,
    ice_fuel_efficiency: Optional[float] = None,  # L/100km (metric)
    ice_fuel_tank_capacity: Optional[float] = None,  # liters (metric)
    ice_label: Optional[str] = None,
) -> Optional[EVVehicle]:
    """Create a new vehicle record.

    If device_id is not provided, generates one from the display_name.
    Returns None if a unique constraint is violated (duplicate device_id or vin).
    """
    if not device_id:
        device_id = f"vehicle_{display_name.lower().replace(' ', '_')}"

    vehicle = EVVehicle(
        display_name=display_name,
        make=make,
        model=model,
        year=year,
        trim=trim,
        battery_capacity_kwh=battery_capacity_kwh,
        vin=vin if vin else None,  # Avoid empty string violating unique
        device_id=device_id,
        source_system=source_system,
        ice_fuel_efficiency=ice_fuel_efficiency,
        ice_fuel_tank_capacity=ice_fuel_tank_capacity,
        ice_label=ice_label,
    )
    db.add(vehicle)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def update_vehicle(
    db: AsyncSession,
    vehicle_id: int,
    **kwargs,
) -> Optional[EVVehicle]:
    """Update specified fields on a vehicle. Returns updated vehicle or None."""
    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return None

    allowed_fields = {
        "display_name", "make", "model", "year", "trim",
        "battery_capacity_kwh", "vin", "device_id", "source_system",
        "ice_fuel_efficiency", "ice_fuel_tank_capacity", "ice_label",
    }
    for key, value in kwargs.items():
        if key in allowed_fields:
            setattr(vehicle, key, value)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def delete_vehicle(db: AsyncSession, vehicle_id: int) -> bool:
    """Delete a vehicle by ID.

    Returns True if deleted, False if not found.
    Refuses to delete the currently active vehicle.
    """
    # Check if this is the active vehicle
    active_vid_str = await get_app_setting(db, "active_vehicle_id", "")
    if active_vid_str:
        try:
            if int(active_vid_str) == vehicle_id:
                return False  # Cannot delete the active vehicle
        except ValueError:
            pass

    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return False
    await db.delete(vehicle)
    await db.commit()
    return True


async def get_active_vehicle(db: AsyncSession) -> Optional[EVVehicle]:
    """Return the active vehicle, or None if no vehicle is set active."""
    vehicle_id_str = await get_app_setting(db, "active_vehicle_id", "")
    if not vehicle_id_str:
        return None
    try:
        vehicle_id = int(vehicle_id_str)
    except ValueError:
        return None
    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    return result.scalar_one_or_none()


async def get_active_device_id(db: AsyncSession) -> Optional[str]:
    """Return the active vehicle's device_id, or None (show all data).

    This is the key helper used by ALL route handlers for query scoping.
    When None is returned, queries should show data for all vehicles.
    """
    vehicle = await get_active_vehicle(db)
    return vehicle.device_id if vehicle else None


async def set_active_vehicle(db: AsyncSession, vehicle_id: int) -> bool:
    """Set the active vehicle by ID.

    Validates the vehicle exists before setting.
    Returns True if set, False if vehicle not found.
    """
    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return False
    await set_app_setting(db, "active_vehicle_id", str(vehicle_id))
    return True
