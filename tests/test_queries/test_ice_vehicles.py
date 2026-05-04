"""ICE vehicle query layer validation tests.

Wave 0 stubs — bodies filled in by Plan 02 (web/queries/ice_vehicles.py + DB model).
Each test guards against ImportError so the suite stays green before Wave 1 lands.
"""

import pytest

try:
    from db.models.ice_vehicle import IceVehicle  # noqa: F401
    from web.queries.ice_vehicles import (  # noqa: F401
        create_ice_vehicle,
        delete_ice_vehicle,
        get_all_ice_vehicles,
        get_default_ice_vehicle,
        get_ice_vehicle_by_id,
        set_default_ice_vehicle,
        update_ice_vehicle,
    )
    HAS_ICE_VEHICLES = True
except ImportError:
    HAS_ICE_VEHICLES = False

pytestmark = [pytest.mark.query, pytest.mark.db]


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 creates IceVehicle model + queries")
async def test_get_all_ice_vehicles_round_trip(db_session):
    """Create 2 ICE vehicles, verify list includes them ordered by label."""
    pytest.skip("Wave 1: Plan 02 fills in ICE CRUD round-trip")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 creates IceVehicle model + queries")
async def test_create_get_update_delete_ice_vehicle(db_session):
    """Round-trip a row through create -> get_by_id -> update -> delete."""
    pytest.skip("Wave 1: Plan 02 fills in ICE CRUD round-trip")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 creates IceVehicle model + queries")
async def test_set_default_promotes_one_demotes_others(db_session):
    """Create two rows; set_default_ice_vehicle(row2.id) -> only row2.is_default=True."""
    pytest.skip("Wave 1: Plan 02 enforces single-default invariant via set_default helper")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 creates IceVehicle model + queries")
async def test_get_default_returns_none_when_no_default(db_session):
    """Empty table -> get_default_ice_vehicle returns None."""
    pytest.skip("Wave 1: Plan 02 fills in default-helper assertion")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 creates IceVehicle model + queries")
async def test_delete_default_guard_when_other_rows_exist(db_session):
    """Default + non-default pair; delete_ice_vehicle(default_id) returns False (per RESEARCH §Pitfall 10 / OQ4)."""
    pytest.skip("Wave 1: Plan 02 implements delete-default guard")


@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 creates IceVehicle model + queries")
async def test_partial_unique_index_blocks_two_defaults(db_session):
    """Migration creates uq_ice_vehicles_one_default partial index — second is_default=True insert raises."""
    pytest.skip("Wave 1: Plan 02 migration creates partial unique index")
