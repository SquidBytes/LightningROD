"""upsert_session: one row per charge, with the place taken from the approved location."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargerStall, EVLocationLookup
from tests.factories.locations import LocationFactory, LocationLookupFactory
from tests.factories.networks import NetworkFactory
from tests.factories.sessions import ChargingSessionFactory
from web.services.pipeline.observation import SessionObservation
from web.services.pipeline.sessions import upsert_session
from web.util.timestamps import utc

pytestmark = pytest.mark.db

DEVICE = "your_vin_000000001"
START = datetime(2026, 8, 24, 20, 0, tzinfo=UTC)
END = START + timedelta(hours=2)


def _ha(**overrides) -> SessionObservation:
    values = {
        "session_start_utc": START, "session_end_utc": END, "energy_kwh": 10.0,
        "start_soc": 40.0, "end_soc": 50.0, "charge_type": "AC",
    }
    name = overrides.pop("location_name", None)
    return SessionObservation(DEVICE, END, {**values, **overrides}, "ha_fordpass", name)


def _fcon(**overrides) -> SessionObservation:
    values = {
        "session_start_utc": START + timedelta(minutes=5), "session_end_utc": END,
        "energy_kwh": 10.4, "start_soc": 41.0, "end_soc": 50.0, "charging_voltage": 240.0,
    }
    name = overrides.pop("location_name", None)
    return SessionObservation(DEVICE, END, {**values, **overrides}, "fcon", name)


async def _sessions(db):
    return (
        await db.execute(select(EVChargingSession).where(EVChargingSession.device_id == DEVICE))
    ).scalars().all()


async def test_new_charge_is_inserted_and_stamped_from_the_observation(db_session):
    await upsert_session(db_session, _fcon())

    (session,) = await _sessions(db_session)
    assert session.source_system == "fcon"
    assert utc(session.recorded_at) == END
    assert utc(session.original_timestamp) == START + timedelta(minutes=5)
    assert session.is_complete is True
    assert session.energy_kwh == 10.4


async def test_same_source_again_refreshes_measurements_but_keeps_the_users_place(db_session):
    await upsert_session(db_session, _fcon(location_name="Truck name"))
    (session,) = await _sessions(db_session)
    session.location_name = "Renamed by user"

    await upsert_session(db_session, _fcon(energy_kwh=10.6, location_name="Truck name"))

    (session,) = await _sessions(db_session)
    assert session.energy_kwh == 10.6
    assert session.location_name == "Renamed by user"


@pytest.mark.parametrize("order", [(_ha, _fcon), (_fcon, _ha)], ids=["ha-first", "fcon-first"])
async def test_two_sources_merge_into_one_row_in_either_order(db_session, order):
    """Starts 5 minutes apart and energy within 10% count as the same charge."""
    for make in order:
        await upsert_session(db_session, make())

    (session,) = await _sessions(db_session)
    assert session.source_system == "ha_fordpass"
    # Home Assistant carries Ford's own charge log.
    assert (utc(session.session_start_utc), session.energy_kwh, session.start_soc) == (START, 10.0, 40.0)
    # Only FordConnect reads charger volts.
    assert session.charging_voltage == 240.0


async def test_a_charge_with_different_energy_is_a_separate_session(db_session):
    await upsert_session(db_session, _ha())
    await upsert_session(db_session, _fcon(energy_kwh=20.0))

    assert len(await _sessions(db_session)) == 2


@pytest.mark.parametrize("make", [_ha, _fcon], ids=["ha", "fcon"])
@pytest.mark.parametrize("protected_source", ["manual_entry", "csv_import"])
async def test_manual_and_imported_sessions_are_never_changed(db_session, make, protected_source):
    await ChargingSessionFactory.create(
        db_session, device_id=DEVICE, source_system=protected_source,
        session_start_utc=START + timedelta(minutes=2), energy_kwh=10.0, start_soc=30.0,
    )

    assert await upsert_session(db_session, make()) is None

    (session,) = await _sessions(db_session)
    assert (session.source_system, session.start_soc) == (protected_source, 30.0)


async def test_name_network_and_type_come_from_the_approved_location(db_session):
    network = await NetworkFactory.create(db_session)
    place = await LocationLookupFactory.create(
        db_session, location_name="Home", latitude=10.0, longitude=20.0,
        location_type="home", network_id=network.id, is_verified=True,
    )

    await upsert_session(db_session, _ha(latitude=10.0, longitude=20.0, location_name="Home"))

    (session,) = await _sessions(db_session)
    assert session.location_id == place.id
    assert (session.location_name, session.location_type, session.network_id) == (
        "Home", "home", network.id,
    )


async def test_vehicles_name_is_used_only_when_no_location_resolves(db_session):
    await upsert_session(db_session, _ha(location_name="Truck's name for it"))

    (session,) = await _sessions(db_session)
    assert session.location_id is None
    assert session.location_name == "Truck's name for it"


async def test_verified_location_with_a_different_name_is_not_reused(db_session):
    """Pins today's resolver rule: a renamed place in the vehicle makes a second, unverified location."""
    place = await LocationLookupFactory.create(
        db_session, location_name="Home", latitude=10.0, longitude=20.0, is_verified=True,
    )

    await upsert_session(db_session, _ha(latitude=10.0, longitude=20.0, location_name="Zebra Depot"))

    (session,) = await _sessions(db_session)
    created = await db_session.get(EVLocationLookup, session.location_id)
    assert session.location_id != place.id
    assert (created.location_name, created.is_verified) == ("Zebra Depot", False)


async def _place_with_stalls(db, *stalls: dict) -> EVLocationLookup:
    place = await LocationLookupFactory.create(db, latitude=10.0, longitude=20.0)
    for i, stall in enumerate(stalls):
        db.add(EVChargerStall(location_id=place.id, stall_label=f"S{i}", **stall))
    await db.flush()
    return place


async def test_a_locations_only_stall_fills_the_charger_values(db_session):
    await _place_with_stalls(db_session, {"rated_kw": 11.5, "voltage": 240.0, "amperage": 48.0})

    await upsert_session(db_session, _ha(latitude=10.0, longitude=20.0))

    (session,) = await _sessions(db_session)
    assert (session.charger_rated_kw, session.evse_voltage, session.evse_amperage) == (11.5, 240.0, 48.0)
    assert session.evse_source == "stall_default"


async def test_two_stalls_with_no_default_fill_nothing(db_session):
    await _place_with_stalls(db_session, {"rated_kw": 11.5}, {"rated_kw": 150.0})

    await upsert_session(db_session, _ha(latitude=10.0, longitude=20.0))

    (session,) = await _sessions(db_session)
    assert (session.stall_id, session.charger_rated_kw, session.evse_source) == (None, None, None)


async def test_the_flagged_default_stall_wins_among_several(db_session):
    await _place_with_stalls(db_session, {"rated_kw": 11.5}, {"rated_kw": 150.0, "is_default": True})

    await upsert_session(db_session, _ha(latitude=10.0, longitude=20.0))

    (session,) = await _sessions(db_session)
    assert session.charger_rated_kw == 150.0


async def test_position_zero_zero_is_replaced_by_the_nearest_stored_fix(db_session):
    await LocationFactory.create(
        db_session, device_id=DEVICE, latitude=10.0, longitude=20.0,
        recorded_at=START - timedelta(minutes=3),
    )

    await upsert_session(db_session, _ha(latitude=0.0, longitude=0.0))

    (session,) = await _sessions(db_session)
    assert (session.latitude, session.longitude) == (10.0, 20.0)
    assert session.location_id is not None


async def test_position_zero_zero_with_no_stored_fix_is_left_empty(db_session):
    await upsert_session(db_session, _ha(latitude=0.0, longitude=0.0))

    (session,) = await _sessions(db_session)
    assert (session.latitude, session.longitude, session.location_id) == (None, None, None)
