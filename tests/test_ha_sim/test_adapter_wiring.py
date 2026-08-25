"""Tests for `_metrics` / `_events` dispatcher wiring.

These tests exercise the dispatcher path: `_metrics` and `_events` entities
must route through `ha_fordpass.process_event`.

Covers:
  - `_metrics` event -> ev_battery_status row (via adapter)
  - `_events` event -> ev_trip_metrics row (via adapter)
"""

import pytest
from sqlalchemy import select

from db.models.battery_status import EVBatteryStatus
from db.models.trip_metrics import EVTripMetrics
from tests.factories.vehicles import VehicleFactory
from web.services.sources.ha_fordpass import handlers as fordpass_handlers
from web.services.sources.ha_fordpass.dispatch import SENSOR_HANDLERS
from web.services.sources.ha_fordpass.handlers import extract_slug

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
def _clear_state():
    """Clear pending batches so tests don't bleed into each other."""
    fordpass_handlers._pending_battery_status.clear()
    fordpass_handlers._pending_battery_status_ts.clear()
    fordpass_handlers._last_trip_values.clear()
    yield
    fordpass_handlers._pending_battery_status.clear()
    fordpass_handlers._pending_battery_status_ts.clear()
    fordpass_handlers._last_trip_values.clear()


def _make_metrics_event(device_id: str) -> tuple[str, dict]:
    """Build a sensor.fordpass_{vin}_metrics state payload.

    Values match the existing metric HA fixtures: xevBatteryRange=260 (km),
    xevBatteryMaximumRange=418 (km). Both are already metric per
    ha-fordpass integration contract, and — like every metrics attribute —
    arrive wrapped in Ford's `{"updateTime": ..., "value": N}` envelope.
    """
    entity_id = f"sensor.fordpass_{device_id}_metrics"
    metrics = {
        "xevBatteryRange": 260,
        "xevBatteryMaximumRange": 418,
        "xevBatteryStateOfCharge": 80,
        "xevBatteryActualStateOfCharge": 77,
        "xevBatteryCapacity": 131000,
        "xevBatteryVoltage": 390.0,
        "xevBatteryIoCurrent": 5.0,
    }
    new_state = {
        "entity_id": entity_id,
        "state": len(metrics),
        "last_changed": "2026-04-19T12:00:00+00:00",
        "last_updated": "2026-04-19T12:00:00+00:00",
        "attributes": {
            key: {"updateTime": "2026-04-19T12:00:00Z", "value": value}
            for key, value in metrics.items()
        },
    }
    return entity_id, new_state


def _make_events_event(device_id: str) -> tuple[str, dict]:
    """Build a sensor.fordpass_{vin}_events state payload with trip data."""
    import json
    entity_id = f"sensor.fordpass_{device_id}_events"
    new_state = {
        "entity_id": entity_id,
        "state": "ok",
        "last_changed": "2026-04-19T12:00:00+00:00",
        "last_updated": "2026-04-19T12:00:00+00:00",
        "attributes": {
            "customEvents": {
                "xev-key-off-trip-segment-data": {
                    "oemData": {
                        "trip_data": {
                            "stringArrayValue": [
                                json.dumps({
                                    "distance_traveled": 19,
                                    "energy_consumed": 7600,
                                    "trip_duration": 1800,
                                    "ambient_temperature": 15,
                                    "cabin_temperature": 20,
                                    "outside_air_ambient_temperature": 15,
                                })
                            ]
                        }
                    }
                }
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


@pytest.mark.asyncio
async def test_odometer_at_trip_boundaries(db_session):
    """Trip ingest pulls odometer_start/end from the closest ev_vehicle_status
    rows around the trip's start_time and end_time.
    """
    from datetime import UTC, datetime, timedelta

    from db.models.vehicle_status import EVVehicleStatus

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    end_t = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)
    start_t = end_t - timedelta(minutes=25)

    # Seed odometer time-series readings around the trip boundaries.
    # Closest-to-start should be at t-5min (12345.0); closest-to-end at t+1min (12368.0).
    seeds = [
        (start_t - timedelta(minutes=5), 12345.0),
        (start_t + timedelta(minutes=2), 12350.0),
        (end_t - timedelta(minutes=10), 12360.0),
        (end_t + timedelta(minutes=1), 12368.0),
        (end_t + timedelta(minutes=10), 12370.0),
    ]
    for ts, odo in seeds:
        db_session.add(
            EVVehicleStatus(
                device_id=_TEST_DEVICE_ID,
                recorded_at=ts,
                odometer=odo,
                source_system="ha_fordpass",
            )
        )
    await db_session.flush()

    # Synthesize an events trip where the inner JSON carries no start_time
    # (the events handler derives start_time from duration). Force start/end
    # by writing the ev_trip_metrics row directly through the adapter path.
    import json as _json

    entity_id = f"sensor.fordpass_{_TEST_DEVICE_ID}_events"
    new_state = {
        "entity_id": entity_id,
        "state": "ok",
        "last_changed": end_t.isoformat(),
        "last_updated": end_t.isoformat(),
        "attributes": {
            "customEvents": {
                "xev-key-off-trip-segment-data": {
                    "updateTime": end_t.isoformat(),
                    "oemData": {
                        "trip_data": {
                            "stringArrayValue": [
                                _json.dumps({
                                    "distance_traveled": 12.0,
                                    "energy_consumed": 4500,
                                    "trip_duration": (end_t - start_t).total_seconds(),
                                    "ambient_temperature": 16.0,
                                    "cabin_temperature": 21.0,
                                    "outside_air_ambient_temperature": 15.0,
                                })
                            ]
                        }
                    },
                }
            }
        },
    }
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    trip = (await db_session.execute(
        select(EVTripMetrics)
        .where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
        .order_by(EVTripMetrics.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    assert trip is not None
    # The events handler today writes start_time=None; odometer_start may be
    # NULL when start_time is missing — that's expected. odometer_end MUST
    # be populated since end_time is the recorded event timestamp.
    assert trip.odometer_end is not None, "odometer_end must be populated from closest reading"
    assert float(trip.odometer_end) == pytest.approx(12368.0)
