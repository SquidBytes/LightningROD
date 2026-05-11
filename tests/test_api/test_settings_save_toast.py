"""Smoke tests for settings save toast + row-highlight.

Asserts that each of the 5 row-table save handlers renders both the toast
block (data-auto-dismiss) AND the server-rendered ring-2 ring-success class
on the saved row.
"""

import pytest
from sqlalchemy import delete

from db.models.ice_vehicle import IceVehicle
from db.models.reference import EVChargingNetwork, EVLocationLookup, GasPriceHistory
from db.models.vehicle import EVVehicle
from web.queries.settings import set_app_setting

pytestmark = [pytest.mark.api, pytest.mark.db]


async def test_network_post_renders_ring_and_toast(client, db_session):
    """POST /settings/networks renders ring on the new network row + toast."""
    await db_session.execute(delete(EVChargingNetwork))
    await db_session.flush()
    response = await client.post(
        "/settings/networks",
        data={
            "network_name": "Smoke Net 35-04",
            "cost_per_kwh": "0.35",
            "color": "#47A8E5",
        },
    )
    assert response.status_code == 200
    assert "ring-2 ring-success" in response.text
    assert "data-auto-dismiss=\"3000\"" in response.text


async def test_network_put_renders_ring_and_toast(client, db_session):
    """PUT /settings/networks/{id} renders ring on the edited network row + toast."""
    await db_session.execute(delete(EVChargingNetwork))
    await db_session.flush()
    net = EVChargingNetwork(network_name="ExistingNet", cost_per_kwh=0.25, is_free=False)
    db_session.add(net)
    await db_session.flush()
    response = await client.put(
        f"/settings/networks/{net.id}",
        data={
            "network_name": "ExistingNet",
            "cost_per_kwh": "0.40",
            "color": "#47A8E5",
        },
    )
    assert response.status_code == 200
    assert "ring-2 ring-success" in response.text
    assert "data-auto-dismiss=\"3000\"" in response.text
    assert "Network costs saved." in response.text


async def test_gas_price_post_renders_ring_and_toast(client, db_session):
    """POST /settings/gas-prices renders ring on the saved row + toast."""
    await db_session.execute(delete(GasPriceHistory))
    await db_session.flush()
    await set_app_setting(db_session, "distance_unit", "us")
    response = await client.post(
        "/settings/gas-prices",
        data={
            "year": "2026",
            "month": "5",
            "station_price": "3.49",
            "average_price": "3.79",
        },
    )
    assert response.status_code == 200
    assert "ring-2 ring-success" in response.text
    assert "data-auto-dismiss=\"3000\"" in response.text
    assert "Gas prices saved." in response.text


async def test_vehicle_post_renders_ring_and_toast(client, db_session):
    """POST /settings/vehicles renders ring on the new vehicle row + toast."""
    await db_session.execute(delete(EVVehicle))
    await db_session.flush()
    response = await client.post(
        "/settings/vehicles",
        data={
            "display_name": "Smoke EV 35-04",
            "make": "Ford",
            "model": "Mach-E",
            "year": "2024",
            "device_id": "SMOKE_TOAST_VIN",
        },
    )
    assert response.status_code == 200
    assert "ring-2 ring-success" in response.text
    assert "data-auto-dismiss=\"3000\"" in response.text
    assert "Vehicle saved." in response.text


async def test_ice_vehicle_post_renders_ring_and_toast(client, db_session):
    """POST /settings/ice-vehicles renders ring on the new ICE row + toast."""
    await db_session.execute(delete(IceVehicle))
    await db_session.flush()
    response = await client.post(
        "/settings/ice-vehicles",
        data={
            "label": "Smoke ICE 35-04",
            "fuel_efficiency_display": "8.4",
            "tank_capacity_display": "50.0",
            "is_default": "true",
        },
    )
    assert response.status_code == 200
    assert "ring-2 ring-success" in response.text
    assert "data-auto-dismiss=\"3000\"" in response.text
    assert "ICE vehicle saved." in response.text


async def test_location_post_renders_ring_and_toast(client, db_session):
    """POST /settings/networks/{nid}/locations renders ring on new location row + toast."""
    await db_session.execute(delete(EVLocationLookup))
    await db_session.execute(delete(EVChargingNetwork))
    await db_session.flush()
    net = EVChargingNetwork(network_name="LocSmokeNet", cost_per_kwh=0.30, is_free=False)
    db_session.add(net)
    await db_session.flush()
    response = await client.post(
        f"/settings/networks/{net.id}/locations",
        data={
            "location_name": "Smoke Location 35-04",
            "location_type": "public",
        },
    )
    assert response.status_code == 200
    assert "ring-2 ring-success" in response.text
    assert "data-auto-dismiss=\"3000\"" in response.text
    assert "Locations saved." in response.text
