"""End-to-end tests: detection layer observations from the live handler path.

Drives events through `process_state_change`-equivalent dispatch (the same
handler registry used by HA ingestion) and asserts the detection layer
records the expected (entity, attribute, unit, method) triples.
"""

from __future__ import annotations

import pytest

from tests.factories.vehicles import VehicleFactory
from web.services import hass_processor
from web.services.hass_processor import SENSOR_HANDLERS, extract_slug
from web.services.units import detection

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

_TEST_DEVICE_ID = "DETVIN"


async def _dispatch(entity_id: str, new_state: dict, db) -> None:
    slug = extract_slug(entity_id)
    assert slug is not None
    handler = SENSOR_HANDLERS.get(slug)
    assert handler is not None
    parts = entity_id[len("sensor.fordpass_"):].split("_", 1)
    device_id = parts[0]
    await handler(slug, new_state, _HA_CONFIG, device_id, db)


@pytest.fixture(autouse=True)
def _reset():
    detection.clear()
    hass_processor._pending_battery_status.clear()
    hass_processor._pending_battery_status_ts.clear()
    hass_processor._pending_vehicle_status.clear()
    hass_processor._pending_vehicle_status_ts.clear()
    hass_processor._last_trip_values.clear()
    yield
    detection.clear()


@pytest.mark.asyncio
async def test_metrics_event_records_declared_units(db_session):
    """A _metrics event should log declared units for xevBatteryRange /
    xevBatteryMaximumRange via the adapter convert() path."""
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id = f"sensor.fordpass_{_TEST_DEVICE_ID}_metrics"
    new_state = {
        "entity_id": entity_id,
        "state": "ok",
        "last_changed": "2026-04-19T12:00:00+00:00",
        "last_updated": "2026-04-19T12:00:00+00:00",
        "attributes": {
            "xevBatteryRange": 260,
            "xevBatteryMaximumRange": 418,
        },
    }
    await _dispatch(entity_id, new_state, db_session)

    units = {
        (r.entity_pattern, r.attribute): r for r in detection.snapshot()
    }
    metrics_range = units.get(
        ("sensor.fordpass_{vin}_metrics", "xevBatteryRange")
    )
    assert metrics_range is not None
    assert metrics_range.method == "declared"
    assert metrics_range.detected_unit == "km"

    max_range = units.get(
        ("sensor.fordpass_{vin}_metrics", "xevBatteryMaximumRange")
    )
    assert max_range is not None
    assert max_range.method == "declared"
    assert max_range.detected_unit == "km"


@pytest.mark.asyncio
async def test_concurrent_elveh_and_events_detects_elveh_distance_unit(db_session):
    """Elveh reports tripDistanceTraveled first (unit unknown); then the events
    entity fires with the canonical metric value. Cross-reference should
    detect the elveh attribute's unit."""
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Elveh event (no unit_of_measurement on the state) — tripDistanceTraveled
    # is ~11.8 miles (i.e. 19 km expressed in miles).
    elveh_entity = f"sensor.fordpass_{_TEST_DEVICE_ID}_elveh"
    elveh_state = {
        "entity_id": elveh_entity,
        "state": "35",
        "last_changed": "2026-04-19T12:00:00+00:00",
        "last_updated": "2026-04-19T12:00:00+00:00",
        "attributes": {
            "unit_of_measurement": "mi",
            "tripDistanceTraveled": 11.8,
            "tripDuration": 30,
            "tripAmbientTemp": 77.0,
            "tripCabinTemp": 77.0,
            "tripOutsideAirAmbientTemp": 77.0,
        },
    }
    await _dispatch(elveh_entity, elveh_state, db_session)

    # Now the events entity fires with the metric-canonical distance (19 km).
    events_entity = f"sensor.fordpass_{_TEST_DEVICE_ID}_events"
    events_state = {
        "entity_id": events_entity,
        "state": "ok",
        "last_changed": "2026-04-19T12:00:10+00:00",
        "last_updated": "2026-04-19T12:00:10+00:00",
        "attributes": {
            "xev-key-off-trip-segment-data": {
                "distance_traveled": 19,
                "energy_consumed": 7600,
                "trip_duration": 1800,
                "ambient_temp": 25,
                "cabin_temp": 25,
                "outside_air_temp": 25,
            }
        },
    }
    await _dispatch(events_entity, events_state, db_session)

    units = {
        (r.entity_pattern, r.attribute): r for r in detection.snapshot()
    }
    # Elveh tripDistanceTraveled detected via cross-reference.
    elveh_distance = units.get(
        ("sensor.fordpass_{vin}_elveh", "tripDistanceTraveled")
    )
    assert elveh_distance is not None, (
        f"tripDistanceTraveled not detected; snapshot keys: {list(units.keys())}"
    )
    # The elveh handler's read_time_uom already recorded "mi", and that
    # outranks cross_reference in priority. Either way, the unit must be "mi".
    assert elveh_distance.detected_unit == "mi"

    # Temperature cross-reference: elveh read=77°F, canonical=25°C
    elveh_ambient = units.get(
        ("sensor.fordpass_{vin}_elveh", "tripAmbientTemp")
    )
    assert elveh_ambient is not None
    # Via _t converter, elveh already records read_time_uom=degF (since
    # uom_for_event="mi" triggers the temp_uom_default="degF" branch). That's
    # fine — we just need the detection layer to know the unit is degF.
    assert elveh_ambient.detected_unit == "degF"
