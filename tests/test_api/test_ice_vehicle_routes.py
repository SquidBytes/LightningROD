"""ICE vehicle HTTP route smoke tests + UNIT-01 round-trip assertion.

Routes covered:
  POST   /settings/ice-vehicles                  — create with display-unit input
  GET    /settings/ice-vehicles/{id}/edit        — fetch edit modal with display-unit values
  PUT    /settings/ice-vehicles/{id}             — update with display-unit input
  DELETE /settings/ice-vehicles/{id}             — delete (with default-row guard)
  POST   /settings/ice-vehicles/{id}/set-default — promote to default
"""

import pytest
from sqlalchemy import delete, select

from db.models.ice_vehicle import IceVehicle
from web.queries.settings import set_app_setting

pytestmark = [pytest.mark.api, pytest.mark.db]


async def _clear_ice_vehicles(db):
    """Wipe ice_vehicles to start each test from a known state.

    SQLite + savepoint isolation can leak the partial unique index state
    across the connection cache; explicit delete keeps the partial unique
    index on is_default predictable.
    """
    await db.execute(delete(IceVehicle))
    await db.flush()


async def test_post_ice_vehicle_creates_row(client, db_session):
    """POST /settings/ice-vehicles with valid form -> 200, row created."""
    await _clear_ice_vehicles(db_session)
    response = await client.post(
        "/settings/ice-vehicles",
        data={
            "label": "Honda Civic",
            "fuel_efficiency_display": 8.4,
            "tank_capacity_display": 50.0,
            "is_default": "true",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeIceVehicleModal"
    result = await db_session.execute(
        select(IceVehicle).where(IceVehicle.label == "Honda Civic")
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    assert rows[0].is_default is True


async def test_unit_01_us_input_writes_metric(client, db_session):
    """UNIT-01: POST fuel_efficiency_display=28 (US units) writes ~8.40 L/100km to DB.

    Conversion: to_metric_fuel_efficiency(28, 'us') = 235.215 / 28 = 8.40 L/100km.
    Asserts the route handler ran the write-time conversion before the query layer.
    """
    await _clear_ice_vehicles(db_session)
    await set_app_setting(db_session, "distance_unit", "us")
    response = await client.post(
        "/settings/ice-vehicles",
        data={
            "label": "Test US Vehicle",
            "fuel_efficiency_display": 28,
            "tank_capacity_display": 14,
        },
    )
    assert response.status_code == 200
    result = await db_session.execute(
        select(IceVehicle).where(IceVehicle.label == "Test US Vehicle")
    )
    row = result.scalar_one()
    assert float(row.fuel_efficiency_l_per_100km) == pytest.approx(8.40, rel=0.01)


async def test_unit_01_round_trip_us_units(client, db_session):
    """UNIT-01 round-trip: POST 28 (US), GET edit form, assert rendered ~= 28.0."""
    await _clear_ice_vehicles(db_session)
    await set_app_setting(db_session, "distance_unit", "us")
    response = await client.post(
        "/settings/ice-vehicles",
        data={
            "label": "Round Trip ICE",
            "fuel_efficiency_display": 28,
        },
    )
    assert response.status_code == 200
    result = await db_session.execute(
        select(IceVehicle).where(IceVehicle.label == "Round Trip ICE")
    )
    row = result.scalar_one()
    edit_response = await client.get(f"/settings/ice-vehicles/{row.id}/edit")
    assert edit_response.status_code == 200
    assert b'value="28.0"' in edit_response.content


async def test_put_ice_vehicle_updates_row(client, db_session):
    """PUT /settings/ice-vehicles/{id} with new label -> row updated, HX-Trigger header present."""
    await _clear_ice_vehicles(db_session)
    ice = IceVehicle(
        label="Original Label",
        fuel_efficiency_l_per_100km=8.4,
        tank_capacity_l=50.0,
        is_default=False,
    )
    db_session.add(ice)
    await db_session.flush()
    await db_session.commit()
    ice_id = ice.id

    response = await client.put(
        f"/settings/ice-vehicles/{ice_id}",
        data={
            "label": "Updated",
            "fuel_efficiency_display": 8.4,
            "tank_capacity_display": 50.0,
        },
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeIceVehicleModal"
    await db_session.refresh(ice)
    assert ice.label == "Updated"


async def test_set_default_promotes_row(client, db_session):
    """POST /settings/ice-vehicles/{id}/set-default flips is_default and demotes others."""
    await _clear_ice_vehicles(db_session)
    ice_default = IceVehicle(
        label="Default ICE",
        fuel_efficiency_l_per_100km=8.4,
        is_default=True,
    )
    ice_other = IceVehicle(
        label="Other ICE",
        fuel_efficiency_l_per_100km=10.0,
        is_default=False,
    )
    db_session.add_all([ice_default, ice_other])
    await db_session.flush()
    await db_session.commit()
    other_id = ice_other.id

    response = await client.post(f"/settings/ice-vehicles/{other_id}/set-default")
    assert response.status_code == 200

    await db_session.refresh(ice_other)
    await db_session.refresh(ice_default)
    assert ice_other.is_default is True
    assert ice_default.is_default is False


async def test_delete_default_guard_returns_422_alert(client, db_session):
    """DELETE on default row when others exist returns alert-error and keeps both rows."""
    await _clear_ice_vehicles(db_session)
    ice_default = IceVehicle(
        label="Guarded Default",
        fuel_efficiency_l_per_100km=8.4,
        is_default=True,
    )
    ice_other = IceVehicle(
        label="Sibling ICE",
        fuel_efficiency_l_per_100km=10.0,
        is_default=False,
    )
    db_session.add_all([ice_default, ice_other])
    await db_session.flush()
    await db_session.commit()
    default_id = ice_default.id

    response = await client.delete(f"/settings/ice-vehicles/{default_id}")
    assert response.status_code == 200
    assert b"Cannot delete the default" in response.content

    result = await db_session.execute(select(IceVehicle))
    rows = list(result.scalars().all())
    assert len(rows) == 2
