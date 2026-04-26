"""Vehicles query layer validation tests.

Tests vehicle listing, active vehicle retrieval, and vehicle creation.
"""

import pytest

from web.queries.settings import set_app_setting
from web.queries.vehicles import (
    get_active_vehicle,
    get_all_vehicles,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_get_all_vehicles(db_session):
    """Create 2 vehicles -> verify list includes them, ordered by display_name."""
    from db.models.vehicle import EVVehicle

    db = db_session

    # Get baseline count (migrations may have seeded vehicles)
    baseline = await get_all_vehicles(db)
    baseline_count = len(baseline)

    v1 = EVVehicle(
        device_id="VEH_001", display_name="Bravo Vehicle",
        vin="VEH_001", source_system="test",
    )
    v2 = EVVehicle(
        device_id="VEH_002", display_name="Alpha Vehicle",
        vin="VEH_002", source_system="test",
    )
    db.add_all([v1, v2])
    await db.flush()

    vehicles = await get_all_vehicles(db)

    assert len(vehicles) == baseline_count + 2
    # Verify our vehicles exist and are sorted by display_name
    names = [v.display_name for v in vehicles]
    # "Alpha Vehicle" should come before "Bravo Vehicle"
    alpha_idx = names.index("Alpha Vehicle")
    bravo_idx = names.index("Bravo Vehicle")
    assert alpha_idx < bravo_idx


async def test_get_active_vehicle(db_session):
    """Set active vehicle -> verify get_active_vehicle returns it."""
    from db.models.vehicle import EVVehicle

    db = db_session

    v = EVVehicle(
        device_id="ACTIVE_VIN", display_name="Active Vehicle",
        vin="ACTIVE_VIN", source_system="test",
    )
    db.add(v)
    await db.flush()

    await set_app_setting(db, "active_vehicle_id", str(v.id))

    active = await get_active_vehicle(db)

    assert active is not None
    assert active.device_id == "ACTIVE_VIN"


async def test_get_active_vehicle_none(db_session):
    """Active vehicle set to empty string -> returns None."""
    # Clear any pre-existing active vehicle setting
    await set_app_setting(db_session, "active_vehicle_id", "")

    result = await get_active_vehicle(db_session)
    assert result is None
