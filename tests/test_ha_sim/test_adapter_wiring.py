"""Tests for `_metrics` / `_events` dispatcher wiring and elveh fallback dedupe.

These tests exercise the dispatcher path: `_metrics` and `_events` entities
must route through `ha_fordpass.process_event`, and the legacy elveh handler
must defer to the adapter when it has recently seen those entities.

Covers:
  - `_metrics` event -> ev_battery_status row (via adapter)
  - `_events` event -> ev_trip_metrics row (via adapter)
  - elveh event arriving after `_metrics` does NOT double-write
    `hv_battery_range` / `hv_battery_max_range`
  - elveh event arriving after `_events` does NOT double-write
    ev_trip_metrics rows
"""

import pytest
from sqlalchemy import select, func

from db.models.battery_status import EVBatteryStatus
from db.models.trip_metrics import EVTripMetrics
from tests.factories.vehicles import VehicleFactory
from tests.test_ha_sim.simulator import make_trip_event
from web.services import hass_processor
from web.services.hass_processor import (
    SENSOR_HANDLERS,
    _flush_battery_status,
    _last_events_seen_ts,
    _last_metrics_seen_ts,
    extract_slug,
)

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

_HA_CONFIG = {
    "location_name": "Test Home",
    "time_zone": "America/New_York",
    "unit_system": {
        "length": "mi",
        "mass": "lb",
        "temperature": "\u00b0F",
        "volume": "gal",
    },
}

_TEST_DEVICE_ID = "TESTVIN29"


async def _dispatch_event(entity_id: str, new_state: dict, db) -> None:
    """Invoke the handler registered for entity_id's slug."""
    slug = extract_slug(entity_id)
    assert slug is not None, f"Could not extract slug from {entity_id}"
    handler = SENSOR_HANDLERS.get(slug)
    assert handler is not None, f"No handler registered for slug: {slug}"
    parts = entity_id[len("sensor.fordpass_"):].split("_", 1)
    device_id = parts[0]
    await handler(slug, new_state, _HA_CONFIG, device_id, db)


@pytest.fixture(autouse=True)
def _clear_adapter_sentinels():
    """Reset module-level sentinels so tests don't bleed into each other."""
    _last_metrics_seen_ts.clear()
    _last_events_seen_ts.clear()
    # Also clear pending batches to start each test with a clean slate.
    hass_processor._pending_battery_status.clear()
    hass_processor._pending_battery_status_ts.clear()
    hass_processor._last_trip_values.clear()
    yield
    _last_metrics_seen_ts.clear()
    _last_events_seen_ts.clear()
    hass_processor._pending_battery_status.clear()
    hass_processor._pending_battery_status_ts.clear()
    hass_processor._last_trip_values.clear()


def _make_metrics_event(device_id: str) -> tuple[str, dict]:
    """Build a sensor.fordpass_{vin}_metrics state payload.

    Values match the existing metric HA fixtures: xevBatteryRange=260 (km),
    xevBatteryMaximumRange=418 (km). Both are already metric per
    ha-fordpass integration contract.
    """
    entity_id = f"sensor.fordpass_{device_id}_metrics"
    new_state = {
        "entity_id": entity_id,
        "state": "ok",
        "last_changed": "2026-04-19T12:00:00+00:00",
        "last_updated": "2026-04-19T12:00:00+00:00",
        "attributes": {
            "xevBatteryRange": 260,
            "xevBatteryMaximumRange": 418,
            "xevBatteryStateOfCharge": 80,
            "xevBatteryActualStateOfCharge": 77,
            "xevBatteryCapacity": 131000,
            "xevBatteryVoltage": 390.0,
            "xevBatteryAmperage": 5.0,
            "xevBatteryPower": 1950,
        },
    }
    return entity_id, new_state


def _make_events_event(device_id: str) -> tuple[str, dict]:
    """Build a sensor.fordpass_{vin}_events state payload with trip data."""
    entity_id = f"sensor.fordpass_{device_id}_events"
    new_state = {
        "entity_id": entity_id,
        "state": "ok",
        "last_changed": "2026-04-19T12:00:00+00:00",
        "last_updated": "2026-04-19T12:00:00+00:00",
        "attributes": {
            "xev-key-off-trip-segment-data": {
                "distance_traveled": 19,
                "energy_consumed": 7600,
                "trip_duration": 1800,
                "ambient_temp": 15,
                "cabin_temp": 20,
                "outside_air_temp": 15,
            }
        },
    }
    return entity_id, new_state


@pytest.mark.asyncio
async def test_metrics_event_writes_battery_status(db_session):
    """A `_metrics` event must write a metric-canonical ev_battery_status row."""
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, new_state = _make_metrics_event(_TEST_DEVICE_ID)
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVBatteryStatus)
        .where(EVBatteryStatus.device_id == _TEST_DEVICE_ID)
        .order_by(EVBatteryStatus.id.desc())
        .limit(1)
    )
    battery = result.scalar_one_or_none()
    assert battery is not None, "adapter did not write ev_battery_status row"
    assert float(battery.hv_battery_range) == pytest.approx(260.0, abs=0.5)
    assert float(battery.hv_battery_max_range) == pytest.approx(418.0, abs=0.5)
    assert battery.ingest_schema_version == 2

    # Sentinel must be recorded for subsequent elveh dedupe.
    assert _TEST_DEVICE_ID in _last_metrics_seen_ts


@pytest.mark.asyncio
async def test_events_event_writes_trip_metrics(db_session):
    """An `_events` event must write a metric-canonical ev_trip_metrics row."""
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, new_state = _make_events_event(_TEST_DEVICE_ID)
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVTripMetrics)
        .where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
        .order_by(EVTripMetrics.id.desc())
        .limit(1)
    )
    trip = result.scalar_one_or_none()
    assert trip is not None, "adapter did not write ev_trip_metrics row"
    assert float(trip.distance) == pytest.approx(19.0, abs=0.1)
    # energy_consumed: 7600 Wh -> 7.6 kWh
    assert float(trip.energy_consumed) == pytest.approx(7.6, abs=0.05)
    assert trip.ingest_schema_version == 2

    # Sentinel must be recorded.
    assert _TEST_DEVICE_ID in _last_events_seen_ts


@pytest.mark.asyncio
async def test_elveh_defers_hv_range_after_metrics(db_session):
    """After a `_metrics` event, a follow-up elveh event must NOT write
    hv_battery_range or hv_battery_max_range to the pending batch.

    The adapter's row is authoritative; the legacy handler must be silent
    on those fields when metrics has fired within the dedupe window.
    """
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Step 1: metrics event -> adapter writes row + sets sentinel.
    metrics_entity, metrics_state = _make_metrics_event(_TEST_DEVICE_ID)
    await _dispatch_event(metrics_entity, metrics_state, db_session)
    await db_session.flush()

    # Baseline row count after adapter write.
    pre_count = (await db_session.execute(
        select(func.count()).select_from(EVBatteryStatus)
        .where(EVBatteryStatus.device_id == _TEST_DEVICE_ID)
    )).scalar_one()
    assert pre_count == 1, "adapter should have written exactly one ev_battery_status row"

    # Step 2: elveh event with state=162 (mi) would previously write
    # hv_battery_range~=261 km. With dedupe active, the field must not be
    # added to the pending batch.
    elveh_entity, elveh_state = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=10.0,
        duration_minutes=15.0,
    )
    # Explicitly set unit_of_measurement="mi" so _read_time_uom returns it.
    elveh_state["attributes"]["unit_of_measurement"] = "mi"
    elveh_state["state"] = "162"

    await _dispatch_event(elveh_entity, elveh_state, db_session)

    # The elveh handler accumulates battery fields in a pending dict.
    # Inspect it directly: hv_battery_range / hv_battery_max_range must not
    # be present because the metrics sentinel short-circuited that branch.
    pending = hass_processor._pending_battery_status.get(_TEST_DEVICE_ID, {})
    assert "hv_battery_range" not in pending, (
        f"elveh fallback wrote hv_battery_range despite recent metrics event: {pending}"
    )
    assert "hv_battery_max_range" not in pending, (
        f"elveh fallback wrote hv_battery_max_range despite recent metrics event: {pending}"
    )
    # Motor-only fields should still pass through (elveh-exclusive).
    assert "motor_voltage" in pending
    assert "motor_amperage" in pending
    assert "motor_kw" in pending

    # And importantly: flushing produces no extra ev_battery_status row with
    # the dedupe'd range value.
    await _flush_battery_status(_TEST_DEVICE_ID, db_session)
    await db_session.flush()

    post_count = (await db_session.execute(
        select(func.count()).select_from(EVBatteryStatus)
        .where(EVBatteryStatus.device_id == _TEST_DEVICE_ID)
    )).scalar_one()
    # A second row may have been written for the motor_* SI fields -- that
    # is fine, elveh retains coverage of those. The important invariant is
    # that NO ev_battery_status row exists where hv_battery_range came from
    # the elveh state (~161 km instead of 260 km from metrics).
    all_rows = (await db_session.execute(
        select(EVBatteryStatus)
        .where(EVBatteryStatus.device_id == _TEST_DEVICE_ID)
        .order_by(EVBatteryStatus.id)
    )).scalars().all()
    adapter_row = all_rows[0]
    assert float(adapter_row.hv_battery_range) == pytest.approx(260.0, abs=0.5)
    for row in all_rows[1:]:
        assert row.hv_battery_range is None, (
            f"post-metrics elveh row carries hv_battery_range={row.hv_battery_range}; "
            "should have been skipped by the dedupe window"
        )
        assert row.hv_battery_max_range is None, (
            f"post-metrics elveh row carries hv_battery_max_range={row.hv_battery_max_range}"
        )
    # post_count assertion: at most two rows, and only the adapter's carries range data.
    assert post_count <= 2


@pytest.mark.asyncio
async def test_elveh_defers_trip_block_after_events(db_session):
    """After an `_events` event, a follow-up elveh event must NOT write a
    second ev_trip_metrics row. The adapter's trip row is authoritative.
    """
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Step 1: events event via dispatcher -> adapter writes trip row + sets sentinel.
    events_entity, events_state = _make_events_event(_TEST_DEVICE_ID)
    await _dispatch_event(events_entity, events_state, db_session)
    await db_session.flush()

    pre_count = (await db_session.execute(
        select(func.count()).select_from(EVTripMetrics)
        .where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
    )).scalar_one()
    assert pre_count == 1, "adapter should have written exactly one trip row"

    # Step 2: elveh event carries its own tripDistanceTraveled + tripEnergyConsumed.
    # Without dedupe this writes a second trip row.
    elveh_entity, elveh_state = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=22.5,
        duration_minutes=35.0,
        efficiency=3.1,
        energy_consumed=7.2,
    )
    await _dispatch_event(elveh_entity, elveh_state, db_session)
    await db_session.flush()

    post_count = (await db_session.execute(
        select(func.count()).select_from(EVTripMetrics)
        .where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
    )).scalar_one()
    assert post_count == 1, (
        f"elveh fallback double-wrote trip row despite recent events event "
        f"(pre={pre_count}, post={post_count})"
    )
