"""Telemetry-derive repair: NULL-fill trip fields from the odometer timeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.trips import TripFactory
from tests.factories.vehicle_status import VehicleStatusFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair.ops.telemetry_derive import TelemetryDerive

pytestmark = [pytest.mark.unit, pytest.mark.db]

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _seed_timeline(db, device: str, end_odo: float, distance: float):
    """Odometer timeline: start reading 30 min before T0, end reading 2 min before."""
    start_odo = end_odo - distance
    readings = [
        (T0 - timedelta(minutes=40), start_odo - 5.0),  # pre-trip, below start
        (T0 - timedelta(minutes=30), start_odo),  # trip start
        (T0 - timedelta(minutes=15), start_odo + distance / 2),  # mid-trip
        (T0 - timedelta(minutes=2), end_odo),  # trip end anchor
    ]
    for recorded_at, odometer in readings:
        await VehicleStatusFactory.create(
            db, device_id=device, recorded_at=recorded_at, odometer=odometer
        )


async def test_derives_timing_odometers_and_temps_from_timeline(db_session):
    device = "DERIVE_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        energy_consumed=6.0,
        efficiency=5.0,
        start_time=None,
        duration=None,
        odometer_start=None,
        odometer_end=None,
        outside_air_temp=None,
        end_time=T0,
    )
    await _seed_timeline(db_session, device, end_odo=1030.0, distance=30.0)

    op = TelemetryDerive()
    assert await op.census(db_session) == 1

    result = await op.apply(db_session)
    assert result.affected == 1
    assert float(trip.odometer_end) == pytest.approx(1030.0)
    assert float(trip.odometer_start) == pytest.approx(1000.0)
    assert trip.start_time == T0 - timedelta(minutes=30)
    assert float(trip.duration) == pytest.approx(1800.0)  # seconds
    # Factory statuses carry outside_temperature=15.0 within the trip window.
    assert float(trip.outside_air_temp) == pytest.approx(15.0)
    assert result.details["filled"]["duration"] == 1
    assert await op.census(db_session) == 0  # idempotent


async def test_plausibility_gate_blocks_implausible_timing(db_session):
    device = "DERIVE_FAST_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    # 150 km over the timeline's 30 min window -> 300 km/h implied.
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=150.0,
        energy_consumed=30.0,
        efficiency=None,
        start_time=None,
        duration=None,
        odometer_start=None,
        odometer_end=None,
        end_time=T0,
    )
    await _seed_timeline(db_session, device, end_odo=5150.0, distance=150.0)

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 1
    # Timing withheld; odometers and efficiency still filled.
    assert trip.start_time is None
    assert trip.duration is None
    assert float(trip.odometer_end) == pytest.approx(5150.0)
    assert float(trip.odometer_start) == pytest.approx(5000.0)
    assert float(trip.efficiency) == pytest.approx(150.0 / 30.0)


async def test_legacy_homeassistant_repaired_manual_never_selected(db_session):
    device = "DERIVE_LEGACY_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    legacy = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="homeassistant",
        distance=30.0,
        duration=None,
        start_time=None,
        odometer_start=None,
        odometer_end=None,
        end_time=T0,
    )
    manual = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="manual_entry",
        distance=30.0,
        duration=None,
        start_time=None,
        odometer_start=None,
        odometer_end=None,
        end_time=T0,
    )
    await _seed_timeline(db_session, device, end_odo=1030.0, distance=30.0)

    op = TelemetryDerive()
    assert [r.id for r in await op.affected_rows(db_session)] == [legacy.id]

    result = await op.apply(db_session)
    assert result.affected == 1
    assert float(legacy.duration) == pytest.approx(1800.0)
    assert manual.duration is None
    assert manual.odometer_end is None


async def test_non_null_fields_never_overwritten(db_session):
    device = "DERIVE_KEEP_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    # Stored odometers disagree with telemetry; only start_time is NULL.
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        duration=2000.0,
        start_time=None,
        odometer_start=998.0,
        odometer_end=1029.0,
        end_time=T0,
    )
    await _seed_timeline(db_session, device, end_odo=1030.0, distance=32.0)

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 1
    # Stored duration pins start exactly (end - duration), overriding the
    # odometer heuristic.
    assert trip.start_time == T0 - timedelta(seconds=2000)
    # Stored values survive untouched.
    assert float(trip.duration) == pytest.approx(2000.0)
    assert float(trip.odometer_start) == pytest.approx(998.0)
    assert float(trip.odometer_end) == pytest.approx(1029.0)


async def test_efficiency_filled_only_with_valid_distance_and_energy(db_session):
    device = "DERIVE_EFF_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    fillable = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=40.0,
        energy_consumed=8.0,
        efficiency=None,
        start_time=T0 - timedelta(minutes=30),
        duration=1800.0,
        odometer_start=1000.0,
        odometer_end=1040.0,
        outside_air_temp=12.0,
        cabin_temp=21.0,
        end_time=T0,
    )
    no_energy = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=40.0,
        energy_consumed=None,
        efficiency=None,
        start_time=T0 - timedelta(minutes=30),
        duration=1800.0,
        odometer_start=2000.0,
        odometer_end=2040.0,
        outside_air_temp=12.0,
        cabin_temp=21.0,
        end_time=T0,
    )

    op = TelemetryDerive()
    assert [r.id for r in await op.affected_rows(db_session)] == [fillable.id]

    await op.apply(db_session)
    # Same formula ingestion stores: distance / energy (km/kWh).
    assert float(fillable.efficiency) == pytest.approx(40.0 / 8.0)
    assert no_energy.efficiency is None


async def test_stored_duration_pins_start_without_telemetry(db_session):
    """duration present + start NULL -> start = end - duration; no status rows needed."""
    device = "DERIVE_PIN_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        duration=1234.0,
        start_time=None,
        odometer_start=1000.0,
        odometer_end=1030.0,
        end_time=T0,
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 1
    assert trip.start_time == T0 - timedelta(seconds=1234)


async def test_preview_has_no_side_effects_and_apply_snapshots(db_session):
    device = "DERIVE_PREVIEW_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        duration=None,
        start_time=None,
        odometer_start=None,
        odometer_end=None,
        end_time=T0,
    )
    await _seed_timeline(db_session, device, end_odo=1030.0, distance=30.0)

    op = TelemetryDerive()
    diffs = await op.preview(db_session)
    assert len(diffs) == 1
    assert diffs[0].action == "update"
    assert diffs[0].before["duration"] is None
    assert diffs[0].after["duration"] == pytest.approx(1800.0)
    # Preview mutated nothing.
    assert trip.duration is None
    assert trip.odometer_end is None

    result = await op.apply(db_session)
    assert result.snapshot_rows > 0
    assert result.run_id is not None
    assert float(trip.duration) == pytest.approx(1800.0)
