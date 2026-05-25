"""ICE vehicle query layer validation tests."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.models.ice_vehicle import IceVehicle
from web.queries.ice_vehicles import (
    create_ice_vehicle,
    delete_ice_vehicle,
    get_all_ice_vehicles,
    get_default_ice_vehicle,
    get_ice_vehicle_by_id,
    set_default_ice_vehicle,
    update_ice_vehicle,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_get_all_ice_vehicles_round_trip(db_session):
    """Create 2 ICE vehicles, verify list includes them ordered by label."""
    db = db_session
    baseline = await get_all_ice_vehicles(db)
    baseline_count = len(baseline)

    db.add(
        IceVehicle(
            label="Bravo Truck",
            fuel_efficiency_l_per_100km=11.5,
            tank_capacity_l=80.0,
            is_default=False,
        )
    )
    db.add(
        IceVehicle(
            label="Alpha Sedan",
            fuel_efficiency_l_per_100km=8.4,
            tank_capacity_l=50.0,
            is_default=False,
        )
    )
    await db.flush()

    rows = await get_all_ice_vehicles(db)
    assert len(rows) == baseline_count + 2
    labels = [r.label for r in rows]
    alpha_idx = labels.index("Alpha Sedan")
    bravo_idx = labels.index("Bravo Truck")
    assert alpha_idx < bravo_idx


async def test_create_get_update_delete_ice_vehicle(db_session):
    """Round-trip a row through create -> get_by_id -> update -> delete."""
    db = db_session
    row = await create_ice_vehicle(
        db,
        label="Test Vehicle",
        fuel_efficiency_l_per_100km=8.4,
        tank_capacity_l=50.0,
        is_default=False,
    )
    assert row is not None
    assert row.id is not None

    fetched = await get_ice_vehicle_by_id(db, row.id)
    assert fetched is not None
    assert fetched.label == "Test Vehicle"

    updated = await update_ice_vehicle(db, row.id, label="Updated Label")
    assert updated is not None
    assert updated.label == "Updated Label"

    ok = await delete_ice_vehicle(db, row.id)
    assert ok is True
    assert await get_ice_vehicle_by_id(db, row.id) is None


async def test_set_default_promotes_one_demotes_others(db_session):
    """Create two rows; set_default_ice_vehicle(row2.id) -> only row2.is_default=True."""
    db = db_session
    row1 = await create_ice_vehicle(
        db, label="First", fuel_efficiency_l_per_100km=9.0, is_default=False
    )
    row2 = await create_ice_vehicle(
        db, label="Second", fuel_efficiency_l_per_100km=10.0, is_default=False
    )
    assert row1 is not None and row2 is not None

    ok = await set_default_ice_vehicle(db, row2.id)
    assert ok is True

    default = await get_default_ice_vehicle(db)
    assert default is not None
    assert default.id == row2.id

    fresh1 = await get_ice_vehicle_by_id(db, row1.id)
    fresh2 = await get_ice_vehicle_by_id(db, row2.id)
    assert fresh1 is not None and fresh2 is not None
    assert fresh1.is_default is False
    assert fresh2.is_default is True


async def test_get_default_returns_none_when_no_default(db_session):
    """Empty table -> get_default_ice_vehicle returns None."""
    db = db_session
    # Demote any pre-existing defaults so the assertion is robust to fixture state.
    rows = await get_all_ice_vehicles(db)
    for r in rows:
        r.is_default = False
    await db.flush()

    row = await create_ice_vehicle(
        db,
        label="No Default Row",
        fuel_efficiency_l_per_100km=8.0,
        is_default=False,
    )
    assert row is not None

    assert await get_default_ice_vehicle(db) is None


async def test_delete_default_guard_when_other_rows_exist(db_session):
    """Default + non-default pair; delete_ice_vehicle(default_id) returns False."""
    db = db_session
    default = await create_ice_vehicle(
        db, label="Default Truck", fuel_efficiency_l_per_100km=11.2, is_default=True
    )
    other = await create_ice_vehicle(
        db, label="Other Sedan", fuel_efficiency_l_per_100km=8.4, is_default=False
    )
    assert default is not None and other is not None

    ok = await delete_ice_vehicle(db, default.id)
    assert ok is False

    still_there = await get_ice_vehicle_by_id(db, default.id)
    assert still_there is not None
    assert still_there.is_default is True


async def test_partial_unique_index_blocks_two_defaults(db_session):
    """Migration creates uq_ice_vehicles_one_default — second is_default=True insert raises."""
    db = db_session
    # Demote any pre-existing defaults so the conflict is solely from the rows we add.
    existing = await get_all_ice_vehicles(db)
    for r in existing:
        r.is_default = False
    await db.flush()

    db.add(
        IceVehicle(
            label="First Default",
            fuel_efficiency_l_per_100km=9.0,
            is_default=True,
        )
    )
    await db.flush()

    db.add(
        IceVehicle(
            label="Second Default",
            fuel_efficiency_l_per_100km=10.0,
            is_default=True,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    # After rollback, queries still work (sanity check; rollback reset state)
    _ = await db.execute(select(IceVehicle))
