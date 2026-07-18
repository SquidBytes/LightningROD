"""API tests for the hide-short-trips setting."""

import pytest
from sqlalchemy import select

from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.queries.settings import get_app_setting, set_app_setting

pytestmark = pytest.mark.db


async def _seed_short_and_long(db_session):
    """One junk key-on trip and one real trip for the active vehicle."""
    vehicle = await VehicleFactory.create(db_session)
    short = await TripFactory.create(
        db_session, device_id=vehicle.device_id, duration=60.0, distance=0.5
    )
    long = await TripFactory.create(
        db_session, device_id=vehicle.device_id, duration=1800.0, distance=25.0
    )
    return short, long


async def _enable_hide(client, minutes="5", distance="3"):
    response = await client.post(
        "/settings/trip-display",
        data={
            "trip_hide_enabled": "on",
            "trip_hide_min_duration": minutes,
            "trip_hide_min_distance": distance,
        },
    )
    assert response.status_code == 200
    return response


async def test_default_off_lists_all_trips(client, db_session):
    """With the setting off (default), short trips stay listed and no note renders."""
    await _seed_short_and_long(db_session)
    response = await client.get("/driving/sessions?range=all")
    assert response.status_code == 200
    assert '<p class="text-3xl font-bold">2</p>' in response.text
    assert "short trip" not in response.text


async def test_enabled_hides_short_trip_and_keeps_db_row(client, db_session):
    """Enabling the filter drops the short trip from the list but not the DB."""
    short, _ = await _seed_short_and_long(db_session)
    await _enable_hide(client)

    response = await client.get("/driving/sessions?range=all")
    assert response.status_code == 200
    # Only the long trip counts; the note explains the missing row
    assert '<p class="text-3xl font-bold">1</p>' in response.text
    assert "1 short trip hidden" in response.text

    row = (
        await db_session.execute(
            select(EVTripMetrics).where(EVTripMetrics.id == short.id)
        )
    ).scalar_one_or_none()
    assert row is not None


async def test_driving_analytics_respect_filter(client, db_session):
    """/driving/performance aggregates exclude hidden trips."""
    await _seed_short_and_long(db_session)

    response = await client.get("/driving/performance?range=all")
    assert response.status_code == 200
    assert ">2 trips</p>" in response.text

    await _enable_hide(client)
    response = await client.get("/driving/performance?range=all")
    assert response.status_code == 200
    assert ">1 trips</p>" in response.text


async def test_settings_round_trip_converts_display_units(client, db_session):
    """US display pref: entering 3 mi stores ~4.83 km; minutes store as seconds."""
    await set_app_setting(db_session, "distance_unit", "us")
    response = await _enable_hide(client, minutes="5", distance="3")

    stored_km = float(await get_app_setting(db_session, "trip_hide_min_distance_km"))
    assert stored_km == pytest.approx(4.83, abs=0.01)
    stored_s = float(await get_app_setting(db_session, "trip_hide_min_duration_s"))
    assert stored_s == 300.0
    assert await get_app_setting(db_session, "trip_hide_enabled") == "true"
    # Re-rendered partial shows the value back in display units
    assert 'value="3.0"' in response.text
