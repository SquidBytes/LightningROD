"""Query helpers for vehicles."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.vehicle import EVVehicle
from web.queries.settings import get_app_setting, set_app_setting

# Structured vehicle presets for cascading combo-box auto-fill.
#
# Each entry carries BOTH the usable and gross pack capacity because the two
# values serve different calculations:
#
#   - battery_usable_kwh -> what energy_kwh can be compared against (drive
#     efficiency, gas-equivalent fallback in comparisons.py). This is the
#     "driver-facing" number you see on marketing material.
#   - battery_gross_kwh  -> total installed cell capacity. FordPass reports
#     this via the `maximumBatteryCapacity` attribute, and battery health /
#     degradation math on /battery must compare against it (otherwise a fresh
#     pack reads >100% health).
#
# Row shape (Phase 27+):
#   make, model, trim_level, battery_option, battery_usable_kwh,
#   battery_gross_kwh, year_min, year_max (year_min == year_max for one row
#   per model year). The (trim, year_min, year_max) shape from Phase 22 is
#   gone — plan 27-03 split trim into trim_level + battery_option.
#
# Authoritative source: RESEARCH.md §2 (Lightning, 34 rows) + §3 (Mach-E,
# 29 rows). Keep this list synced with that table when battery specs shift.
VEHICLE_PRESETS: list[dict[str, Any]] = [
    # -----------------------------------------------------------------
    # Ford F-150 Lightning — 34 rows, MY2022-MY2026 (RESEARCH.md §2)
    # -----------------------------------------------------------------
    # MY2022 — 7 rows
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Platinum", "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2022, "year_max": 2022},
    # MY2023 — 7 rows
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Platinum", "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2023, "year_max": 2023},
    # MY2024 — 8 rows (Flash debuts)
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Flash",    "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Platinum", "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2024, "year_max": 2024},
    # MY2025 — 8 rows (Flash uses lower-capacity ER-123 pack per RESEARCH §2)
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Flash",    "battery_option": "Extended Range", "battery_usable_kwh": 123.0, "battery_gross_kwh": 135.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Standard Range", "battery_usable_kwh": 98.0,  "battery_gross_kwh": 108.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Platinum", "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2025, "year_max": 2025},
    # MY2026 — 5 rows (SR discontinued fleet-wide; Pro/XLT/Flash on ER-123, Lariat/Platinum retain full ER)
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Pro",      "battery_option": "Extended Range", "battery_usable_kwh": 123.0, "battery_gross_kwh": 135.0, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "XLT",      "battery_option": "Extended Range", "battery_usable_kwh": 123.0, "battery_gross_kwh": 135.0, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Flash",    "battery_option": "Extended Range", "battery_usable_kwh": 123.0, "battery_gross_kwh": 135.0, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Lariat",   "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "F-150 Lightning", "trim_level": "Platinum", "battery_option": "Extended Range", "battery_usable_kwh": 131.0, "battery_gross_kwh": 143.0, "year_min": 2026, "year_max": 2026},

    # -----------------------------------------------------------------
    # Ford Mustang Mach-E — 29 rows, MY2021-MY2026 (RESEARCH.md §3)
    # -----------------------------------------------------------------
    # MY2021 — 5 rows (NCM SR 68/75.7, ER 88/98.8)
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Select",             "battery_option": "Standard Range", "battery_usable_kwh": 68.0, "battery_gross_kwh": 75.7, "year_min": 2021, "year_max": 2021},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Standard Range", "battery_usable_kwh": 68.0, "battery_gross_kwh": 75.7, "year_min": 2021, "year_max": 2021},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Extended Range", "battery_usable_kwh": 88.0, "battery_gross_kwh": 98.8, "year_min": 2021, "year_max": 2021},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "California Route 1", "battery_option": "Extended Range", "battery_usable_kwh": 88.0, "battery_gross_kwh": 98.8, "year_min": 2021, "year_max": 2021},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "GT",                 "battery_option": "Extended Range", "battery_usable_kwh": 88.0, "battery_gross_kwh": 98.8, "year_min": 2021, "year_max": 2021},
    # MY2022 — 5 rows (same packs as MY2021)
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Select",             "battery_option": "Standard Range", "battery_usable_kwh": 68.0, "battery_gross_kwh": 75.7, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Standard Range", "battery_usable_kwh": 68.0, "battery_gross_kwh": 75.7, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Extended Range", "battery_usable_kwh": 88.0, "battery_gross_kwh": 98.8, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "California Route 1", "battery_option": "Extended Range", "battery_usable_kwh": 88.0, "battery_gross_kwh": 98.8, "year_min": 2022, "year_max": 2022},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "GT",                 "battery_option": "Extended Range", "battery_usable_kwh": 88.0, "battery_gross_kwh": 98.8, "year_min": 2022, "year_max": 2022},
    # MY2023 — 5 rows (LFP SR 70/72 transition, ER usable grows to 91)
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Select",             "battery_option": "Standard Range", "battery_usable_kwh": 70.0, "battery_gross_kwh": 72.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Standard Range", "battery_usable_kwh": 70.0, "battery_gross_kwh": 72.0, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "California Route 1", "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2023, "year_max": 2023},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "GT",                 "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2023, "year_max": 2023},
    # MY2024 — 5 rows (LFP SR 72/73, Cal Route 1 discontinued, Rally debuts)
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Select",             "battery_option": "Standard Range", "battery_usable_kwh": 72.0, "battery_gross_kwh": 73.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Standard Range", "battery_usable_kwh": 72.0, "battery_gross_kwh": 73.0, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "GT",                 "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2024, "year_max": 2024},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Rally",              "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2024, "year_max": 2024},
    # MY2025 — 5 rows (same as MY2024)
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Select",             "battery_option": "Standard Range", "battery_usable_kwh": 72.0, "battery_gross_kwh": 73.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Standard Range", "battery_usable_kwh": 72.0, "battery_gross_kwh": 73.0, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "GT",                 "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2025, "year_max": 2025},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Rally",              "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2025, "year_max": 2025},
    # MY2026 — 5 rows (same as MY2025)
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Select",             "battery_option": "Standard Range", "battery_usable_kwh": 72.0, "battery_gross_kwh": 73.0, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Standard Range", "battery_usable_kwh": 72.0, "battery_gross_kwh": 73.0, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Premium",            "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "GT",                 "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2026, "year_max": 2026},
    {"make": "Ford", "model": "Mustang Mach-E", "trim_level": "Rally",              "battery_option": "Extended Range", "battery_usable_kwh": 91.0, "battery_gross_kwh": 98.8, "year_min": 2026, "year_max": 2026},
]


def lookup_battery_values(
    make: str,
    model: str,
    year: int,
    trim_level: str,
    battery_option: str,
) -> tuple[float, float] | None:
    """Return (battery_usable_kwh, battery_gross_kwh) for a preset match or None.
    Match rule: exact equality on make/model/trim_level/battery_option, and
    year_min <= year <= year_max. Consumed by cascade auto-fill.
    """
    for row in VEHICLE_PRESETS:
        if (
            row["make"] == make
            and row["model"] == model
            and row["trim_level"] == trim_level
            and row["battery_option"] == battery_option
            and row["year_min"] <= year <= row["year_max"]
        ):
            return (row["battery_usable_kwh"], row["battery_gross_kwh"])
    return None


async def get_all_vehicles(db: AsyncSession) -> list[EVVehicle]:
    """Return all vehicles ordered by display_name."""
    result = await db.execute(
        select(EVVehicle).order_by(EVVehicle.display_name)
    )
    return list(result.scalars().all())


async def get_vehicle_by_id(
    db: AsyncSession, vehicle_id: int
) -> EVVehicle | None:
    """Return a single vehicle by ID, or None if not found."""
    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    return result.scalar_one_or_none()


async def create_vehicle(
    db: AsyncSession,
    display_name: str,
    make: str | None = None,
    model: str | None = None,
    year: int | None = None,
    trim_level: str | None = None,
    battery_option: str | None = None,
    battery_capacity_kwh: float | None = None,
    battery_gross_capacity_kwh: float | None = None,
    vin: str | None = None,
    device_id: str | None = None,
    source_system: str | None = None,
    ice_fuel_efficiency: float | None = None,  # L/100km (metric)
    ice_fuel_tank_capacity: float | None = None,  # liters (metric)
    ice_label: str | None = None,
) -> EVVehicle | None:
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
        trim_level=trim_level,
        battery_option=battery_option,
        battery_capacity_kwh=battery_capacity_kwh,
        battery_gross_capacity_kwh=battery_gross_capacity_kwh,
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
) -> EVVehicle | None:
    """Update specified fields on a vehicle. Returns updated vehicle or None."""
    result = await db.execute(
        select(EVVehicle).where(EVVehicle.id == vehicle_id)
    )
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return None

    allowed_fields = {
        "display_name", "make", "model", "year", "trim_level", "battery_option",
        "battery_capacity_kwh", "battery_gross_capacity_kwh",
        "vin", "device_id", "source_system",
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


async def get_active_vehicle(db: AsyncSession) -> EVVehicle | None:
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


async def get_active_device_id(db: AsyncSession) -> str | None:
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
