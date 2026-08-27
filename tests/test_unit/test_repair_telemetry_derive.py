"""Telemetry-derive repair: NULL-fill trip fields from the odometer timeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.locations import LocationFactory, LocationLookupFactory
from tests.factories.trips import TripFactory
from tests.factories.vehicle_status import VehicleStatusFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair.ops.telemetry_derive import TelemetryDerive

pytestmark = [pytest.mark.unit, pytest.mark.db]

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

# Endpoints ~1 km apart so a start snapshot never geo-matches the end lookup.
START_COORDS = (45.5000, -122.6000)
END_COORDS = (45.5100, -122.6500)
FAR_COORDS = (46.0000, -123.0000)


async def _seed_gps(db, device: str, coords, recorded_at) -> None:
    """A single GPS snapshot at coords."""
    await LocationFactory.create(
        db, device_id=device, latitude=coords[0], longitude=coords[1], recorded_at=recorded_at
    )


async def _seed_known_place(db, device: str, coords, recorded_at):
    """A GPS snapshot plus the known lookup it geo-matches; returns the lookup."""
    lookup = await LocationLookupFactory.create(
        db, latitude=coords[0], longitude=coords[1]
    )
    await _seed_gps(db, device, coords, recorded_at)
    return lookup


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


async def _seed_ignition_timeline(db, device: str, end_odo: float, rows):
    """Sparse variant: ignition rows plus a lone end odometer anchor at T0-2min.

    rows: iterable of (recorded_at, ignition_status, odometer|None).
    """
    for recorded_at, ignition, odometer in rows:
        await VehicleStatusFactory.create(
            db,
            device_id=device,
            recorded_at=recorded_at,
            ignition_status=ignition,
            odometer=odometer,
        )
    await VehicleStatusFactory.create(
        db, device_id=device, recorded_at=T0 - timedelta(minutes=2), odometer=end_odo
    )


async def _ignition_trip(db, device: str, distance: float = 30.0):
    return await TripFactory.create(
        db,
        device_id=device,
        source_system="ha_fordpass",
        distance=distance,
        duration=None,
        start_time=None,
        odometer_start=None,
        odometer_end=None,
        end_time=T0,
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
    preview = await op.preview(db_session)
    (group,) = preview.groups
    diffs = preview.diffs
    assert len(diffs) == 1
    assert diffs[0].action == "update"
    assert diffs[0].before["duration"] is None
    assert diffs[0].after["duration"] == pytest.approx(1800.0)

    # Every derived field explains where its value came from.
    assert set(diffs[0].notes) == set(diffs[0].after)
    assert "1800" in diffs[0].notes["duration"]
    assert "odometer" in diffs[0].notes["start_time"]
    assert "vehicle status odometer" in diffs[0].notes["odometer_end"]
    assert "distance" in diffs[0].notes["odometer_start"]
    assert group.context["derives"] == ", ".join(diffs[0].after)
    # Preview mutated nothing.
    assert trip.duration is None
    assert trip.odometer_end is None

    result = await op.apply(db_session)
    assert result.snapshot_rows > 0
    assert result.run_id is not None
    assert float(trip.duration) == pytest.approx(1800.0)


async def test_ignition_fallback_fills_start_when_odometer_sparse(db_session):
    """No pre-end odometer readings -> latest OFF->ON transition pins start."""
    device = "DERIVE_IGN_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=30.0)
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=1030.0,
        rows=[
            (T0 - timedelta(hours=3), "OFF", None),
            (T0 - timedelta(minutes=25), "ON", None),
        ],
    )

    op = TelemetryDerive()
    # The weakest inference of the three must show its working before applying.
    (diff,) = (await op.preview(db_session)).diffs
    assert "ignition OFF->ON" in diff.notes["start_time"]
    assert "1500" in diff.notes["duration"]

    result = await op.apply(db_session)
    assert result.affected == 1
    assert trip.start_time == T0 - timedelta(minutes=25)
    assert float(trip.duration) == pytest.approx(1500.0)
    assert result.details["filled"]["start_time"] == 1
    assert result.details["filled"]["start_time_ignition"] == 1
    assert await op.census(db_session) == 0  # idempotent


async def test_ignition_fallback_uses_latest_transition(db_session):
    device = "DERIVE_IGN_LATEST_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=25.0)
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=2025.0,
        rows=[
            (T0 - timedelta(hours=5), "OFF", None),
            (T0 - timedelta(hours=4), "ON", None),
            (T0 - timedelta(hours=1), "OFF", None),
            (T0 - timedelta(minutes=30), "ON", None),
        ],
    )

    op = TelemetryDerive()
    await op.apply(db_session)
    assert trip.start_time == T0 - timedelta(minutes=30)


async def test_ignition_fallback_respects_plausibility_gate(db_session):
    # 30 km in 9 min -> 200 km/h implied; timing must stay NULL. The ON row
    # sits > IGNITION_ODO_WINDOW from the anchor so no cross-check interferes.
    device = "DERIVE_IGN_FAST_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=30.0)
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=3030.0,
        rows=[
            (T0 - timedelta(hours=3), "OFF", None),
            (T0 - timedelta(minutes=9), "ON", None),
        ],
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert trip.start_time is None
    assert trip.duration is None
    assert "start_time_ignition" not in result.details["filled"]
    # Odometer anchors still filled.
    assert float(trip.odometer_end) == pytest.approx(3030.0)
    assert float(trip.odometer_start) == pytest.approx(3000.0)


async def test_ignition_fallback_rejected_by_odometer_cross_check(db_session):
    """ON row odometer far above odo_start -> the transition is not this trip."""
    device = "DERIVE_IGN_XCHK_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=30.0)
    # odo_start = 4030 - 30 = 4000; ON row reads 4020 (> 4000 + 0.5 tolerance).
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=4030.0,
        rows=[
            (T0 - timedelta(hours=3), "OFF", None),
            (T0 - timedelta(minutes=25), "ON", 4020.0),
        ],
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert trip.start_time is None
    assert trip.duration is None
    assert "start_time_ignition" not in result.details["filled"]


async def test_ignition_scans_back_past_destination_key_cycle(db_session):
    """Newest ON is a key-cycle at the destination; the odometer-matching
    earlier transition is the real trip start. The plateau path must lose:
    its only candidate reading is days old (gate-fails), and the true start
    ON reads just above the plateau tolerance but within the ignition match."""
    device = "DERIVE_IGN_SCAN_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=30.0)
    # odo_start = 6030 - 30 = 6000.
    # Plateau candidate: 5990 two days ago -> 0.6 km/h, gate-rejected.
    await VehicleStatusFactory.create(
        db_session,
        device_id=device,
        recorded_at=T0 - timedelta(hours=47),
        odometer=5990.0,
    )
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=6030.0,
        rows=[
            (T0 - timedelta(minutes=50), "OFF", None),
            # 6000.7: above plateau's odo_start+0.5 but within the ignition
            # two-sided match (1.0 km).
            (T0 - timedelta(minutes=35), "ON", 6000.7),
            (T0 - timedelta(minutes=8), "OFF", 6030.0),
            (T0 - timedelta(minutes=7), "ON", 6030.0),
        ],
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert trip.start_time == T0 - timedelta(minutes=35)
    assert float(trip.duration) == pytest.approx(2100.0)
    assert result.details["filled"]["start_time_ignition"] == 1


async def test_ignition_rejects_transition_far_below_start_odometer(db_session):
    """An old journey's ON (odometer well below odo_start) must not pin start,
    even when the implied speed would pass the plausibility gate. The one-sided
    check accepted this; the two-sided match must not."""
    device = "DERIVE_IGN_OLD_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=100.0)
    # odo_start = 7100 - 100 = 7000. The plateau path sees only a reading
    # from 40h ago (2.5 km/h, gate-rejected); the lone ON at -10h reads 6800
    # (an older journey), implying a plausible 10 km/h -> only the two-sided
    # odometer match can reject it.
    await VehicleStatusFactory.create(
        db_session,
        device_id=device,
        recorded_at=T0 - timedelta(hours=40),
        odometer=6800.0,
    )
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=7100.0,
        rows=[
            (T0 - timedelta(hours=11), "OFF", None),
            (T0 - timedelta(hours=10), "ON", 6800.0),
        ],
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert trip.start_time is None
    assert trip.duration is None
    assert "start_time_ignition" not in result.details["filled"]


async def test_backfills_location_ids_from_gps_history(db_session):
    """GPS near start & end that geo-match known places -> both FK ids persisted."""
    device = "DERIVE_LOC_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        energy_consumed=6.0,
        efficiency=5.0,
        start_time=T0 - timedelta(minutes=30),
        duration=1800.0,
        odometer_start=1000.0,
        odometer_end=1030.0,
        outside_air_temp=12.0,
        cabin_temp=21.0,
        start_location_id=None,
        end_location_id=None,
        end_time=T0,
    )
    start_lookup = await _seed_known_place(
        db_session, device, START_COORDS, T0 - timedelta(minutes=30)
    )
    end_lookup = await _seed_known_place(
        db_session, device, END_COORDS, T0 - timedelta(minutes=1)
    )

    op = TelemetryDerive()
    assert await op.census(db_session) == 1

    result = await op.apply(db_session)
    assert result.affected == 1
    assert trip.start_location_id == start_lookup.id
    assert trip.end_location_id == end_lookup.id
    assert result.details["filled"]["start_location_id"] == 1
    assert result.details["filled"]["end_location_id"] == 1
    assert await op.census(db_session) == 0  # idempotent


async def test_location_raw_coord_fallback_never_fabricates_fk(db_session):
    """GPS near end but far from any known place -> FK stays NULL."""
    device = "DERIVE_LOC_RAW_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        efficiency=5.0,
        start_time=T0 - timedelta(minutes=30),
        duration=1800.0,
        odometer_start=1000.0,
        odometer_end=1030.0,
        outside_air_temp=12.0,
        cabin_temp=21.0,
        end_time=T0,
    )
    # A known place exists but sits far from the trip's GPS snapshot.
    await LocationLookupFactory.create(
        db_session, latitude=FAR_COORDS[0], longitude=FAR_COORDS[1]
    )
    await _seed_gps(db_session, device, END_COORDS, T0 - timedelta(minutes=1))

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 0  # nothing else to fill; no fabricated FK
    assert trip.start_location_id is None
    assert trip.end_location_id is None


async def test_location_not_filled_without_gps_in_tolerance(db_session):
    """No GPS within 30 min of either endpoint -> FKs NULL."""
    device = "DERIVE_LOC_NOGPS_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        efficiency=5.0,
        start_time=T0 - timedelta(minutes=30),
        duration=1800.0,
        odometer_start=1000.0,
        odometer_end=1030.0,
        outside_air_temp=12.0,
        cabin_temp=21.0,
        end_time=T0,
    )
    # GPS at a known place, but recorded two hours before trip end (out of window).
    await _seed_known_place(db_session, device, END_COORDS, T0 - timedelta(hours=2))

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 0
    assert trip.start_location_id is None
    assert trip.end_location_id is None


async def test_location_fk_never_overwritten(db_session):
    """A set start_location_id survives; only the NULL end FK is filled."""
    device = "DERIVE_LOC_KEEP_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=30.0,
        efficiency=5.0,
        start_time=T0 - timedelta(minutes=30),
        duration=1800.0,
        odometer_start=1000.0,
        odometer_end=1030.0,
        outside_air_temp=12.0,
        cabin_temp=21.0,
        start_location_id=999,
        end_location_id=None,
        end_time=T0,
    )
    await _seed_known_place(db_session, device, START_COORDS, T0 - timedelta(minutes=30))
    end_lookup = await _seed_known_place(
        db_session, device, END_COORDS, T0 - timedelta(minutes=1)
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 1
    assert trip.start_location_id == 999  # never overwritten
    assert trip.end_location_id == end_lookup.id


async def test_location_backfill_uses_freshly_derived_start(db_session):
    """Timing and location land in one apply: GPS keyed to the derived start."""
    device = "DERIVE_LOC_DERIVED_VIN"
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
        outside_air_temp=12.0,
        cabin_temp=21.0,
        end_time=T0,
    )
    await _seed_timeline(db_session, device, end_odo=1030.0, distance=30.0)
    # GPS at the odometer-derived start (T0-30min) and at trip end.
    start_lookup = await _seed_known_place(
        db_session, device, START_COORDS, T0 - timedelta(minutes=30)
    )
    end_lookup = await _seed_known_place(
        db_session, device, END_COORDS, T0 - timedelta(minutes=1)
    )

    op = TelemetryDerive()
    result = await op.apply(db_session)
    assert result.affected == 1
    assert trip.start_time == T0 - timedelta(minutes=30)
    assert trip.start_location_id == start_lookup.id
    assert trip.end_location_id == end_lookup.id


async def test_ignition_unsupported_rows_do_not_break_pairing(db_session):
    device = "DERIVE_IGN_UNSUP_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip = await _ignition_trip(db_session, device, distance=30.0)
    await _seed_ignition_timeline(
        db_session,
        device,
        end_odo=5030.0,
        rows=[
            (T0 - timedelta(hours=3), "Off", None),
            (T0 - timedelta(hours=2), "Unsupported", None),
            (T0 - timedelta(hours=1), "Unsupported", None),
            (T0 - timedelta(minutes=25), "On", None),
        ],
    )

    op = TelemetryDerive()
    await op.apply(db_session)
    assert trip.start_time == T0 - timedelta(minutes=25)
    assert float(trip.duration) == pytest.approx(1500.0)
