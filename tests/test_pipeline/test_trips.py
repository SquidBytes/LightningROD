"""upsert_trip: one row per drive, whichever source reports it and in whatever order."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from web.services.pipeline.observation import TripObservation
from web.services.pipeline.trips import upsert_trip
from web.util.timestamps import utc

pytestmark = pytest.mark.db

DEVICE = "your_vin_000000001"
HA_ID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
FCON_ID = uuid.UUID("00000000-0000-4000-8000-0000000000fc")


def _at(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 24, 12, minute, second, tzinfo=UTC)


def _ha(trip_id: uuid.UUID = HA_ID, **overrides) -> TripObservation:
    values = {
        "start_time": _at(0), "end_time": _at(30, 10), "duration": 1800.0,
        "distance": 20.4, "energy_consumed": 5.1, "charge_regenerated": 3.0, "cabin_temp": 21.0,
    }
    return TripObservation(DEVICE, trip_id, {**values, **overrides}, "ha_fordpass")


def _fcon(trip_id: uuid.UUID = FCON_ID, **overrides) -> TripObservation:
    values = {
        "start_time": _at(1), "end_time": _at(30), "duration": 1740.0, "distance": 20.0,
        "odometer_start": 30000.0, "odometer_end": 30020.0, "energy_consumed": 5.4,
        "brake_torque": 900.0,
    }
    return TripObservation(DEVICE, trip_id, {**values, **overrides}, "fcon")


async def _trips(db):
    return (
        await db.execute(select(EVTripMetrics).where(EVTripMetrics.device_id == DEVICE))
    ).scalars().all()


async def test_new_drive_is_inserted_and_stamped_from_the_observation(db_session):
    await upsert_trip(db_session, _fcon())

    (trip,) = await _trips(db_session)
    assert trip.trip_id == FCON_ID
    assert trip.source_system == "fcon"
    assert utc(trip.recorded_at) == _at(30)
    assert utc(trip.original_timestamp) == _at(1)
    assert trip.is_complete is True
    assert trip.ingest_schema_version == 2
    assert (trip.distance, trip.odometer_end) == (20.0, 30020.0)


async def test_same_source_resending_its_own_drive_refreshes_the_row(db_session):
    await upsert_trip(db_session, _fcon())
    await upsert_trip(db_session, _fcon(distance=21.0, odometer_end=30021.0))

    (trip,) = await _trips(db_session)
    assert (trip.distance, trip.odometer_end) == (21.0, 30021.0)


@pytest.mark.parametrize("order", [(_ha, _fcon), (_fcon, _ha)], ids=["ha-first", "fcon-first"])
async def test_two_sources_merge_into_one_row_in_either_order(db_session, order):
    for make in order:
        await upsert_trip(db_session, make())

    (trip,) = await _trips(db_session)
    assert trip.trip_id == HA_ID
    assert trip.source_system == "ha_fordpass"
    # FordConnect: ignition-off time, odometer, brake torque.
    assert utc(trip.end_time) == _at(30)
    assert (trip.odometer_start, trip.odometer_end) == (30000.0, 30020.0)
    assert trip.brake_torque == 900.0
    # Home Assistant: the vehicle's own duration, distance, energy and extras.
    assert trip.duration == 1800.0
    assert trip.distance == 20.4
    assert trip.energy_consumed == 5.1
    assert (trip.charge_regenerated, trip.cabin_temp) == (3.0, 21.0)
    # Worked out from the merged values: 12:30:00 minus 1800 s, and 20.4 / 5.1.
    assert utc(trip.start_time) == _at(0)
    assert trip.efficiency == 4.0


@pytest.mark.parametrize("make", [_ha, _fcon], ids=["ha", "fcon"])
@pytest.mark.parametrize("protected_source", ["manual_entry", "csv_import"])
async def test_manual_and_imported_trips_are_never_changed(db_session, make, protected_source):
    await TripFactory.create(
        db_session, device_id=DEVICE, source_system=protected_source, start_time=_at(0),
        end_time=_at(30), duration=1800.0, distance=20.0, energy_consumed=5.0, efficiency=4.0,
        cabin_temp=None,
    )

    assert await upsert_trip(db_session, make()) is None

    (trip,) = await _trips(db_session)
    assert trip.source_system == protected_source
    assert (trip.distance, trip.energy_consumed, trip.cabin_temp) == (20.0, 5.0, None)


async def test_same_source_second_report_only_fills_gaps_and_refreshes_regen_and_scores(db_session):
    await upsert_trip(db_session, _ha(charge_regenerated=3.0, speed_score=None))
    later = _ha(
        trip_id=uuid.UUID("00000000-0000-4000-8000-0000000000ab"),
        end_time=_at(30, 40), distance=20.9, energy_consumed=5.5, duration=1830.0,
        charge_regenerated=4.0, range_regenerated=2.5, speed_score=80.0, outside_air_temp=18.0,
    )
    await upsert_trip(db_session, later)

    (trip,) = await _trips(db_session)
    assert trip.trip_id == HA_ID
    assert (utc(trip.end_time), trip.duration) == (_at(30, 10), 1800.0)
    assert (trip.distance, trip.energy_consumed) == (20.4, 5.1)
    assert (trip.charge_regenerated, trip.range_regenerated, trip.speed_score) == (4.0, 2.5, 80.0)
    assert trip.outside_air_temp == 18.0
