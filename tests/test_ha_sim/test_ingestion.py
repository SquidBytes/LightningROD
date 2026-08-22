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
from web.services.sources.ha_fordpass.dispatch import SENSOR_HANDLERS
from web.services.sources.ha_fordpass.handlers import extract_slug

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
    # duration is canonical seconds (35 minutes → 2100 seconds)
    assert float(trip.duration) == 35.0 * 60
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
    assert slug is not None
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


# ---------------------------------------------------------------------------
# Network auto-tag flow: payload UNKNOWN/empty -> inherit from known location;
# payload with network -> auto-learn onto unverified location.
#
# Each test fires a full energytransferlogentry through the dispatch registry,
# mirroring how real FordPass payloads flow from Home Assistant through the
# adapter and into the DB. Coords match the simulator's default GPS so geo
# matching aligns with the seeded location.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_charging_ingestion_inherits_network_from_known_location(db_session):
    """Case 2: payload reports network=UNKNOWN, but GPS matches a pre-existing
    location with a network configured. Session inherits that network.
    """
    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVChargingNetwork, EVLocationLookup

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Seed: verified network + auto-created location pre-tagged with that network
    net = EVChargingNetwork(network_name="Seeded EA", is_verified=True)
    db_session.add(net)
    await db_session.flush()
    loc = EVLocationLookup(
        location_name="Pre-Known Station",
        latitude=38.9072,
        longitude=-77.0369,
        network_id=net.id,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db_session.add(loc)
    await db_session.flush()

    # Payload arrives with no network info — simulates the FordPass
    # "UNKNOWN" / empty network case that was leaving sessions untagged.
    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=18.0,
        charge_type="DC_FAST",
        network_name="UNKNOWN",
        latitude=38.9072,
        longitude=-77.0369,
    )
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVChargingSession).where(EVChargingSession.device_id == _TEST_DEVICE_ID)
    )
    session = result.scalar_one_or_none()

    assert session is not None
    assert session.location_id == loc.id
    assert session.network_id == net.id


@pytest.mark.asyncio
async def test_charging_ingestion_auto_learns_network_onto_unverified_location(db_session):
    """Case 3 (buildout-over-time): an auto-created unverified location with
    network_id=NULL learns the network from the first payload that reports one
    at those coords. Both the session and the location row get tagged.
    """
    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVLocationLookup

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Seed: auto-created stub location with no network attached yet
    loc = EVLocationLookup(
        location_name="Untagged Stub",
        latitude=38.9072,
        longitude=-77.0369,
        network_id=None,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db_session.add(loc)
    await db_session.flush()

    # First named-network payload at this spot — should teach the location
    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=22.0,
        charge_type="DC_FAST",
        network_name="Electrify America",
        latitude=38.9072,
        longitude=-77.0369,
    )
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    # Session is tagged with the resolved network
    result = await db_session.execute(
        select(EVChargingSession).where(EVChargingSession.device_id == _TEST_DEVICE_ID)
    )
    session = result.scalar_one_or_none()
    assert session is not None
    assert session.location_id == loc.id
    assert session.network_id is not None

    # And the location row now carries the same network — buildout learned.
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id == session.network_id


@pytest.mark.asyncio
async def test_charging_ingestion_skips_auto_learn_on_manual_location(db_session):
    """User-touched (source_system='manual') locations are protected: even when
    a payload reports a real network, the location row is not mutated.
    """
    from db.models.reference import EVChargingNetwork, EVLocationLookup

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    # Pre-seed a unique network so resolve_network has a single deterministic
    # match (avoid clashing with the predefined-network seed data).
    net = EVChargingNetwork(network_name="ManualProtectNet", is_verified=True)
    db_session.add(net)
    await db_session.flush()

    # User-approved location at the payload coords with no network set
    loc = EVLocationLookup(
        location_name="My Approved Spot",
        latitude=38.9072,
        longitude=-77.0369,
        network_id=None,
        is_verified=True,
        source_system="manual",
    )
    db_session.add(loc)
    await db_session.flush()

    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=10.0,
        charge_type="DC_FAST",
        network_name="ManualProtectNet",
        latitude=38.9072,
        longitude=-77.0369,
    )
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id is None, (
        "Manual location must not auto-learn a network from a payload"
    )


# ---------------------------------------------------------------------------
# Approved-location tagging. A location renamed in the vehicle must not retag
# the location the user reviewed and approved in LightningROD.
# ---------------------------------------------------------------------------


async def _seed_approved_location(db, *, latitude=38.9072, longitude=-77.0369):
    """A verified location with a curated network, plus a GPS alias for it."""
    from db.models.reference import (
        EVChargingNetwork,
        EVLocationGPSAlias,
        EVLocationLookup,
    )

    net = EVChargingNetwork(network_name="Work", is_verified=True)
    db.add(net)
    await db.flush()
    loc = EVLocationLookup(
        location_name="Riverview Office",
        location_type="work",
        latitude=latitude,
        longitude=longitude,
        network_id=net.id,
        is_verified=True,
        source_system="manual",
    )
    db.add(loc)
    await db.flush()
    db.add(
        EVLocationGPSAlias(
            location_id=loc.id,
            latitude=latitude,
            longitude=longitude,
            source="merge",
        )
    )
    await db.flush()
    return loc, net


@pytest.mark.asyncio
async def test_charging_ingestion_keeps_approved_location_after_vehicle_rename(
    db_session,
):
    """Vehicle renamed the location; the approved name, type and network win."""
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)
    loc, net = await _seed_approved_location(db_session)

    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=12.0,
        charge_type="AC",
        network_name="Work network",
        location_name="Work north",
    )
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    session = (
        await db_session.execute(
            select(EVChargingSession).where(
                EVChargingSession.device_id == _TEST_DEVICE_ID
            )
        )
    ).scalar_one()

    assert session.location_id == loc.id
    assert session.location_name == "Riverview Office"
    assert session.location_type == "work"
    assert session.network_id == net.id
    assert session.charge_type == "AC"


@pytest.mark.asyncio
async def test_charging_ingestion_fills_evse_from_default_stall(db_session):
    """The approved location's default stall supplies voltage and amperage."""
    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVChargerStall

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)
    loc, _ = await _seed_approved_location(db_session)
    stall = EVChargerStall(
        location_id=loc.id,
        stall_label="A1",
        charger_type="L2",
        rated_kw=11.5,
        voltage=240,
        amperage=48,
        is_default=True,
    )
    db_session.add(stall)
    await db_session.flush()

    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=9.0,
        charge_type="AC",
        network_name="Work",
    )
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    session = (
        await db_session.execute(
            select(EVChargingSession).where(
                EVChargingSession.device_id == _TEST_DEVICE_ID
            )
        )
    ).scalar_one()

    assert session.stall_id == stall.id
    assert float(session.evse_voltage) == 240
    assert float(session.evse_amperage) == 48
    assert float(session.charger_rated_kw) == 11.5
    assert session.evse_source == "stall_default"


@pytest.mark.asyncio
async def test_charging_ingestion_falls_back_to_stall_charge_type(db_session):
    """Payload without chargerType borrows AC/DC from the stall spec."""
    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVChargerStall

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)
    loc, _ = await _seed_approved_location(db_session)
    db_session.add(
        EVChargerStall(
            location_id=loc.id,
            stall_label="A1",
            charger_type="DCFC",
            is_default=True,
        )
    )
    await db_session.flush()

    entity_id, new_state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=30.0,
        charge_type="",
        network_name="Work",
    )
    await _dispatch_event(entity_id, new_state, db_session)
    await db_session.flush()

    session = (
        await db_session.execute(
            select(EVChargingSession).where(
                EVChargingSession.device_id == _TEST_DEVICE_ID
            )
        )
    ).scalar_one()

    assert session.charge_type == "DC"
