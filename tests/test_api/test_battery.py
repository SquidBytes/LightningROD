"""Battery temperature chart endpoint + chart-builder tests.

Covers the new battery-temp section endpoint (lazy-loaded chart) and the
chart-builder + query helpers that back it.
"""

from datetime import UTC, datetime, timedelta

import pytest

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle import EVVehicle
from db.models.vehicle_status import EVVehicleStatus
from web.queries.battery import (
    build_battery_temp_chart,
    query_battery_temp_timeline,
    query_outside_temp_timeline,
)
from web.queries.settings import set_app_setting

pytestmark = pytest.mark.db


async def _seed_vehicle(db, device_id="TEMP_VIN", make_active=True):
    v = EVVehicle(
        device_id=device_id,
        display_name="Temp Test Vehicle",
        vin=device_id,
        source_system="test",
    )
    db.add(v)
    await db.flush()
    if make_active:
        await set_app_setting(db, "active_vehicle_id", str(v.id))
    return v


# ---------------------------------------------------------------------------
# Section endpoint integration
# ---------------------------------------------------------------------------


async def test_section_battery_temp_returns_chart(client, db_session):
    """Section endpoint returns a Plotly chart fragment when temp data exists."""
    db = db_session
    await _seed_vehicle(db, device_id="DEV1")

    now = datetime.now(UTC)
    db.add_all([
        EVBatteryStatus(
            device_id="DEV1",
            recorded_at=now - timedelta(hours=2),
            hv_battery_temperature=22.5,
            source_system="test",
        ),
        EVBatteryStatus(
            device_id="DEV1",
            recorded_at=now - timedelta(hours=1),
            hv_battery_temperature=23.0,
            source_system="test",
        ),
        EVVehicleStatus(
            device_id="DEV1",
            recorded_at=now - timedelta(hours=2),
            outside_temperature=15.0,
            source_system="test",
        ),
    ])
    await db.flush()

    r = await client.get("/battery?section=battery_temp&range=7d")
    assert r.status_code == 200
    assert "plotly" in r.text.lower()


async def test_section_battery_temp_empty(client, db_session):
    """Empty data -> verbatim empty-state copy from the UI spec."""
    await _seed_vehicle(db_session, device_id="EMPTY_VIN")

    r = await client.get("/battery?section=battery_temp&range=7d")
    assert r.status_code == 200
    assert "No temperature data available for this time range." in r.text


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def test_query_battery_temp_timeline_returns_rows(db_session):
    """Three rows with hv_battery_temperature -> three dicts with the right keys."""
    now = datetime.now(UTC)
    db_session.add_all([
        EVBatteryStatus(
            device_id="D1",
            recorded_at=now - timedelta(hours=i),
            hv_battery_temperature=20.0 + i,
            source_system="test",
        )
        for i in range(3)
    ])
    await db_session.flush()

    rows = await query_battery_temp_timeline(db_session, time_range="7d", device_id="D1")
    assert len(rows) == 3
    assert all("recorded_at" in r and "hv_battery_temperature" in r for r in rows)


async def test_query_battery_temp_skips_null_temp(db_session):
    """Rows with NULL hv_battery_temperature are filtered out."""
    now = datetime.now(UTC)
    db_session.add_all([
        EVBatteryStatus(
            device_id="D2",
            recorded_at=now - timedelta(hours=1),
            hv_battery_temperature=22.0,
            source_system="test",
        ),
        EVBatteryStatus(
            device_id="D2",
            recorded_at=now - timedelta(hours=2),
            hv_battery_temperature=None,
            source_system="test",
        ),
    ])
    await db_session.flush()

    rows = await query_battery_temp_timeline(db_session, time_range="7d", device_id="D2")
    assert len(rows) == 1


async def test_query_outside_temp_timeline_returns_rows(db_session):
    """Two EVVehicleStatus rows with outside_temperature -> two dicts."""
    now = datetime.now(UTC)
    db_session.add_all([
        EVVehicleStatus(
            device_id="D3",
            recorded_at=now - timedelta(hours=1),
            outside_temperature=10.0,
            source_system="test",
        ),
        EVVehicleStatus(
            device_id="D3",
            recorded_at=now - timedelta(hours=2),
            outside_temperature=12.0,
            source_system="test",
        ),
    ])
    await db_session.flush()

    rows = await query_outside_temp_timeline(db_session, time_range="7d", device_id="D3")
    assert len(rows) == 2
    assert all("recorded_at" in r and "outside_temperature" in r for r in rows)


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------


def test_build_battery_temp_chart_renders_two_traces():
    """Both temp_data and outside_data render -> Battery + Outside Air traces."""
    now = datetime.now(UTC)
    temp_data = [{"recorded_at": now, "hv_battery_temperature": 22.0}]
    outside_data = [{"recorded_at": now, "outside_temperature": 15.0}]
    html = build_battery_temp_chart(temp_data, outside_data, [], temp_label="°C")
    assert html is not None
    assert "Battery" in html
    assert "Outside Air" in html


def test_build_battery_temp_chart_renders_charging_regions():
    """Non-empty charging_regions -> shaded rectangles use the SOC fill colour."""
    now = datetime.now(UTC)
    temp_data = [{"recorded_at": now, "hv_battery_temperature": 22.0}]
    html = build_battery_temp_chart(
        temp_data,
        [],
        [(now - timedelta(hours=1), now)],
        temp_label="°C",
    )
    assert "rgba(74, 222, 128, 0.25)" in html


def test_build_battery_temp_chart_empty_returns_none():
    """No temp data, no outside data -> None (caller renders empty state)."""
    html = build_battery_temp_chart([], [], [], temp_label="°C")
    assert html is None
