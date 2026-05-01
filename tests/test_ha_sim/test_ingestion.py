"""End-to-end ingestion pipeline tests: simulator events -> hass_processor -> DB.

Tests verify that simulated FordPass sensor events, when processed through
the hass_processor handler functions, create correct database records.

These tests call processor handlers directly with the test db_session fixture,
avoiding the AsyncSessionLocal indirection in process_state_change while still
testing the full processing logic.
"""

import pytest
from sqlalchemy import select

from tests.factories.vehicles import VehicleFactory
from tests.test_ha_sim.simulator import (
    make_charging_session_event,
    make_gps_event,
    make_lastrefresh_event,
    make_trip_event,
)
from web.services.hass_processor import (
    SENSOR_HANDLERS,
    extract_slug,
)

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

# Default HA config matching the simulator's config.
# The legacy FordPass preferred-unit flags on ha_config were deleted;
# unit handling now lives in the ha_fordpass adapter FIELD_CONTRACTS
# + to_metric dispatch.
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

_TEST_DEVICE_ID = "TESTVIN001"


async def _dispatch_event(
    entity_id: str, new_state: dict, db, ha_config: dict = _HA_CONFIG
) -> None:
    """Dispatch a simulated event through the processor handler registry.

    Extracts slug from entity_id, looks up the handler, and calls it
    with the test db_session.
    """
    slug = extract_slug(entity_id)
    assert slug is not None, f"Could not extract slug from {entity_id}"
    handler = SENSOR_HANDLERS.get(slug)
    assert handler is not None, f"No handler registered for slug: {slug}"

    device_id = entity_id.split("_")[1] if "_" in entity_id else "unknown"
    # Extract device_id properly: sensor.fordpass_{device_id}_{slug}
    parts = entity_id[len("sensor.fordpass_"):].split("_", 1)
    device_id = parts[0]

    await handler(slug, new_state, ha_config, device_id, db)


@pytest.mark.asyncio
async def test_charging_session_ingestion(db_session):
    """Inject energytransferlogentry event, verify EVChargingSession record created."""
    from db.models.charging_session import EVChargingSession

    # Create vehicle so processor can find it
    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Generate and dispatch charging event
    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=32.5,
        charge_type="DC_FAST",
        network_name="Electrify America",
        start_soc=15.0,
        end_soc=80.0,
    )

    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    # Query DB for the created session
    result = await db_session.execute(
        select(EVChargingSession).where(
            EVChargingSession.device_id == _TEST_DEVICE_ID
        )
    )
    session = result.scalar_one_or_none()

    assert session is not None, "Charging session not created"
    assert session.energy_kwh == 32.5
    # _normalize_charge_type in hass_processor collapses DC_FAST → "DC"
    # (Level 1/2/3 granularity is intentionally discarded; see _CHARGER_TYPE_MAP).
    assert session.charge_type == "DC"
    assert session.start_soc == 15.0
    assert session.end_soc == 80.0
    assert session.source_system == "ha_fordpass"


@pytest.mark.asyncio
async def test_ingestion_captures_charging_temps(db_session):
    """(updated by ): batteryTemperature + outsidetemp
    arrive in degC on the energytransferlogentry payload (matches real
    ha-fordpass integration). They populate EVChargingSession.{battery,ambient}_temp_{start,end}
    in degC via adapter FIELD_CONTRACTS (passthrough, no unit conversion).
    """
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=23.5,
        charge_type="AC_BASIC",
        network_name="Home",
        start_soc=56.0,
        end_soc=80.0,
        battery_temp_c=25.0,     # passthrough to DB
        outside_temp_c=22.25,    # passthrough to DB
    )

    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVChargingSession).where(EVChargingSession.device_id == _TEST_DEVICE_ID)
    )
    session = result.scalar_one_or_none()

    assert session is not None, "Charging session not created"
    assert float(session.battery_temp_start) == pytest.approx(25.0, abs=0.01)
    assert float(session.battery_temp_end) == pytest.approx(25.0, abs=0.01)
    assert float(session.ambient_temp_start) == pytest.approx(22.25, abs=0.01)
    assert float(session.ambient_temp_end) == pytest.approx(22.25, abs=0.01)


@pytest.mark.asyncio
async def test_trip_ingestion(db_session):
    """Inject elveh trip event, verify EVTripMetrics record created."""
    from db.models.trip_metrics import EVTripMetrics

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Generate and dispatch trip event
    entity_id, new_state = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=22.5,
        duration_minutes=35.0,
        efficiency=3.1,
        energy_consumed=7.2,
    )

    await _dispatch_event(entity_id, new_state, db_session)
    # The trip handler commits internally, so we need to check after
    await db_session.flush()

    result = await db_session.execute(
        select(EVTripMetrics).where(
            EVTripMetrics.device_id == _TEST_DEVICE_ID
        )
    )
    trip = result.scalar_one_or_none()

    assert trip is not None, "Trip record not created"
    # Distance should be converted from miles to km (22.5 * 1.60934)
    assert trip.distance is not None
    assert abs(float(trip.distance) - 22.5 * 1.60934) < 0.1
    assert float(trip.duration) == 35.0
    assert trip.source_system == "ha_fordpass"


@pytest.mark.asyncio
async def test_battery_status_ingestion(db_session):
    """Inject battery events + lastrefresh, verify EVBatteryStatus record created."""
    from db.models.battery_status import EVBatteryStatus

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # SOC event to populate pending battery status. The soc entity's own UoM
    # is "%"; the batteryRange attribute's unit comes from an elveh signal
    # elsewhere. With no prior cross-reference and no device_class on soc,
    # the resolver correctly reports unknown and drops hv_battery_range.
    soc_entity = f"sensor.fordpass_{_TEST_DEVICE_ID}_soc"
    soc_state = {
        "state": "75",
        "last_changed": "2024-01-15T10:00:00+00:00",
        "last_updated": "2024-01-15T10:00:00+00:00",
        "attributes": {"batteryRange": 195.0},
    }
    await _dispatch_event(soc_entity, soc_state, db_session)

    # Lastrefresh event to flush accumulated battery status
    refresh_entity, refresh_state = make_lastrefresh_event(device_id=_TEST_DEVICE_ID)
    # lastrefresh handler is in vehicle_status handler
    slug = extract_slug(refresh_entity)
    handler = SENSOR_HANDLERS[slug]
    parts = refresh_entity[len("sensor.fordpass_"):].split("_", 1)
    device_id = parts[0]
    await handler(slug, refresh_state, _HA_CONFIG, device_id, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVBatteryStatus).where(
            EVBatteryStatus.device_id == _TEST_DEVICE_ID
        )
    )
    battery = result.scalar_one_or_none()

    assert battery is not None, "Battery status not created"
    assert float(battery.hv_battery_soc) == 75.0
    # hv_battery_range must be None when no unit signal is available — the
    # resolver never silently defaults to "mi". A later metrics.xevBatteryRange
    # event would back-fill via cross-reference.
    assert battery.hv_battery_range is None
    assert battery.source_system == "ha_fordpass"


@pytest.mark.asyncio
async def test_gps_location_ingestion(db_session):
    """Inject device_tracker GPS event, verify EVLocation record created."""
    from db.models.location import EVLocation

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, new_state = make_gps_event(
        device_id=_TEST_DEVICE_ID,
        lat=38.9072,
        lon=-77.0369,
        accuracy=5.0,
    )

    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVLocation).where(EVLocation.device_id == _TEST_DEVICE_ID)
    )
    loc = result.scalar_one_or_none()

    assert loc is not None, "GPS location not created"
    assert float(loc.latitude) == pytest.approx(38.9072, abs=0.001)
    assert float(loc.longitude) == pytest.approx(-77.0369, abs=0.001)
    assert loc.source_system == "ha_fordpass"
