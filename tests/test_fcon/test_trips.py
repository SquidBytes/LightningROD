"""Trips from FordConnect telemetry: ignition-ON spans in which the odometer moves."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.location import EVLocation
from db.models.trip_metrics import EVTripMetrics
from tests.test_fcon.payloads import telemetry
from web.services.sources.fcon import adapter
from web.services.sources.fcon.raw_store import FconRawArchive
from web.services.sources.fcon.trips import TripSample, derive_trip

T0 = datetime(2026, 8, 30, 7, 48, tzinfo=UTC)
DEVICE = "your_vin_000000001"
ACTUAL = {"location": {"lat": 10.0, "lon": 20.0, "alt": 5.0}, "gpsDimension": "DIM_3D", "gpsCoordinateMethod": "ACTUAL"}


def _payload(minutes: int, ignition: str, odometer: float, **values) -> dict:
    stamp = (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return telemetry(stamp, ignitionStatus=ignition, odometer=odometer, **values)


def _samples(*payloads: dict) -> list[TripSample]:
    out = [TripSample.from_payload(p) for p in payloads]
    assert all(out)
    return out  # type: ignore[return-value]


def _journey_with_park() -> list[dict]:
    """Drive, park with the vehicle still on, drive on, switch off."""
    return [
        _payload(-5, "OFF", 1000.0),
        _payload(0, "ON", 1000.0, xevBatteryEnergyRemaining=80.0, speed=0.0),
        _payload(10, "ON", 1006.0, xevBatteryEnergyRemaining=78.9, speed=50.0, brakeTorque=900.0),
        _payload(20, "ON", 1006.0, xevBatteryEnergyRemaining=78.9, speed=0.0, gearLeverPosition="PARK"),
        _payload(31, "ON", 1011.0, xevBatteryEnergyRemaining=78.0, speed=40.0),
        _payload(41, "OFF", 1013.0, xevBatteryEnergyRemaining=77.5, outsideTemperature=24.0,
                 tripXevBatteryRangeRegenerated=3.5),
    ]


@pytest.mark.unit
def test_mid_journey_park_is_one_trip():
    f = derive_trip(_samples(*_journey_with_park()[1:]))
    assert f is not None
    assert f["start_time"] == T0
    assert f["end_time"] == T0 + timedelta(minutes=41)
    assert f["duration"] == 41 * 60
    assert f["distance"] == 13.0
    assert (f["odometer_start"], f["odometer_end"]) == (1000.0, 1013.0)
    assert f["energy_consumed"] == 2.5
    assert f["efficiency"] == 5.2
    assert f["range_regenerated"] == 3.5
    assert f["ambient_temp"] == 24.0
    assert f["brake_torque"] == 900.0


@pytest.mark.unit
def test_remote_start_without_movement_is_not_a_trip():
    samples = _samples(
        _payload(0, "ON", 1000.0),
        _payload(15, "ON", 1000.0),
        _payload(16, "OFF", 1000.0),
    )
    assert derive_trip(samples) is None


@pytest.mark.unit
def test_still_running_is_not_finished():
    assert derive_trip(_samples(_payload(0, "ON", 1000.0), _payload(5, "ON", 1003.0))) is None


@pytest.mark.unit
def test_odometer_backslide_is_clamped():
    samples = _samples(
        _payload(0, "ON", 1000.0),
        _payload(5, "ON", 1004.0),
        _payload(6, "OFF", 1003.5),
    )
    f = derive_trip(samples)
    assert f["odometer_end"] == 1004.0
    assert f["distance"] == 4.0


async def _feed(db, payloads: list[dict]):
    archive = FconRawArchive()
    for p in payloads:
        if await archive.store(db, 1, "telemetry", p, T0):
            await adapter.process_sample("telemetry", p, db, {"config_id": 1})
    await db.commit()


@pytest.mark.db
async def test_switch_off_writes_one_trip_and_park_does_not_split(db_session):
    journey = _journey_with_park()
    journey[1]["metrics"]["position"] = {"updateTime": journey[1]["updateTime"], "value": ACTUAL}
    await _feed(db_session, journey + [_payload(60, "OFF", 1013.0)])
    trips = (await db_session.execute(
        select(EVTripMetrics).where(EVTripMetrics.device_id == DEVICE)
    )).scalars().all()
    assert len(trips) == 1
    trip = trips[0]
    assert trip.source_system == "fcon"
    assert trip.is_complete
    assert float(trip.distance) == 13.0
    assert float(trip.energy_consumed) == 2.5
    assert trip.cabin_temp is None and trip.driving_score is None
    fixes = (await db_session.execute(
        select(EVLocation).where(EVLocation.device_id == DEVICE)
    )).scalars().all()
    assert len(fixes) == 1 and fixes[0].source_system == "fcon"


@pytest.mark.db
async def test_switching_off_mid_journey_splits(db_session):
    await _feed(db_session, [
        _payload(-5, "OFF", 1000.0),
        _payload(0, "ON", 1000.0),
        _payload(10, "OFF", 1006.0),
        _payload(20, "ON", 1006.0),
        _payload(30, "OFF", 1013.0),
    ])
    distances = sorted(float(d) for d in (await db_session.execute(
        select(EVTripMetrics.distance).where(EVTripMetrics.device_id == DEVICE)
    )).scalars().all())
    assert distances == [6.0, 7.0]


@pytest.mark.db
async def test_reparse_updates_rather_than_duplicates(db_session):
    journey = _journey_with_park()
    await _feed(db_session, journey)
    await adapter.process_sample("telemetry", journey[-1], db_session, {"config_id": 1})
    await db_session.commit()
    count = len((await db_session.execute(
        select(EVTripMetrics.id).where(EVTripMetrics.device_id == DEVICE)
    )).all())
    assert count == 1
