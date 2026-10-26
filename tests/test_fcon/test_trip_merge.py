"""One row per drive when Home Assistant and FordConnect both record it."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from tests.test_fcon.payloads import telemetry
from web.services.sources.fcon import adapter as fcon_adapter
from web.services.sources.fcon.raw_store import FconRawArchive
from web.services.sources.ha_fordpass import handlers as fp_handlers
from web.services.sources.ha_fordpass.adapter import process_sample as ha_process
from web.services.trip_merge import FCON, HA, PRECEDENCE, merge_changes

VIN = "YOURVIN0000000001"
DEVICE = VIN.lower()
T0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
HA_CONFIG = {"unit_system": "metric"}
KEYOFF = (T0 + timedelta(minutes=25, seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fcon(minutes: float, ignition: str, odometer: float, **values) -> dict:
    p = telemetry(_iso(minutes), ignitionStatus=ignition, odometer=odometer, **values)
    p["vin"] = VIN
    return p


FCON_DRIVE = [
    _fcon(-5, "OFF", 1000.0),
    _fcon(0, "ON", 1000.0, xevBatteryEnergyRemaining=80.0),
    _fcon(10, "ON", 1006.0, brakeTorque=500.0),
    _fcon(20, "ON", 1012.0),
    _fcon(25, "OFF", 1013.0, xevBatteryEnergyRemaining=77.4, outsideTemperature=24.0),
]


def _events_state() -> tuple[str, dict]:
    ts = (T0 + timedelta(minutes=25, seconds=30)).isoformat()
    trip = {
        "distance_traveled": 12.6, "energy_consumed": 2400.0, "trip_duration": 1510.0,
        "ambient_temperature": 22.0, "cabin_temperature": 21.0,
        "outside_air_ambient_temperature": 20.0,
    }
    return f"sensor.fordpass_{DEVICE}_events", {
        "state": "key_off", "last_changed": ts, "last_updated": ts,
        "attributes": {"customEvents": {"xev-key-off-trip-segment-data": {
            "updateTime": KEYOFF,
            "oemData": {"trip_data": {"stringArrayValue": [json.dumps(trip)]}},
        }}},
    }


def _elveh_state() -> dict:
    ts = (T0 + timedelta(minutes=25, seconds=40)).isoformat()
    return {
        "state": "300", "last_changed": ts, "last_updated": ts,
        "attributes": {
            "unit_of_measurement": "km",
            "tripDistanceTraveled": 12.6, "tripDuration": "0:25:10",
            "tripEnergyConsumed": 2.4, "tripDrivingScore": 88.0, "tripSpeedScore": 80.0,
            "tripAccelerationScore": 75.0, "tripDecelerationScore": 70.0,
            "tripRangeRegenerated": 3.0, "tripElectricalEfficiency": 90.0,
            "tripUpdateTime": KEYOFF,
        },
    }


async def _feed_fcon(db) -> None:
    archive = FconRawArchive()
    for p in FCON_DRIVE:
        if await archive.store(db, 1, "telemetry", p, T0):
            await fcon_adapter.process_sample("telemetry", p, db, {"config_id": 1})
    await db.flush()


async def _feed_ha(db, *, elveh_first: bool = False) -> None:
    fp_handlers._last_trip_values.clear()
    entity, events = _events_state()

    async def events_step():
        await ha_process(entity, events, db, HA_CONFIG)
        await db.flush()

    async def elveh_step():
        await fp_handlers.handle_battery_status("elveh", _elveh_state(), HA_CONFIG, DEVICE, db)
        await db.flush()

    for step in ((elveh_step, events_step) if elveh_first else (events_step, elveh_step)):
        await step()


async def _trips(db) -> list[EVTripMetrics]:
    rows = (await db.execute(
        select(EVTripMetrics).where(EVTripMetrics.device_id == DEVICE)
    )).scalars().all()
    for r in rows:
        await db.refresh(r)
    return list(rows)


def _f(v) -> float | None:
    return None if v is None else float(v)


def _snapshot(row: EVTripMetrics) -> dict:
    def ts(v):
        return v if v is None or v.tzinfo else v.replace(tzinfo=UTC)

    return {
        "start_time": ts(row.start_time), "end_time": ts(row.end_time),
        "duration": _f(row.duration), "distance": _f(row.distance),
        "energy_consumed": _f(row.energy_consumed), "efficiency": _f(row.efficiency),
        "odometer_start": _f(row.odometer_start), "odometer_end": _f(row.odometer_end),
        "ambient_temp": _f(row.ambient_temp), "cabin_temp": _f(row.cabin_temp),
        "outside_air_temp": _f(row.outside_air_temp),
        "driving_score": _f(row.driving_score), "range_regenerated": _f(row.range_regenerated),
        "electrical_efficiency": _f(row.electrical_efficiency),
        "brake_torque": _f(row.brake_torque), "source_system": row.source_system,
        "trip_id": row.trip_id,
    }


EXPECTED = {
    # The vehicle's own duration back from FCON's ignition-off; FCON's first ON
    # poll can be minutes after the drive began.
    "start_time": T0 + timedelta(minutes=25) - timedelta(seconds=1510),
    "end_time": T0 + timedelta(minutes=25), "duration": 1510.0,
    "distance": 12.6, "energy_consumed": 2.4, "efficiency": 5.25,
    "odometer_start": 1000.0, "odometer_end": 1013.0,
    "ambient_temp": 22.0, "cabin_temp": 21.0, "outside_air_temp": 20.0,
    "driving_score": 88.0, "range_regenerated": 3.0, "electrical_efficiency": 90.0,
    "brake_torque": 500.0, "source_system": HA,
}


@pytest.mark.db
@pytest.mark.parametrize("order", ["ha_then_fcon", "fcon_then_ha", "fcon_then_elveh_first"])
async def test_both_orders_give_one_merged_row(db_session, order):
    if order == "ha_then_fcon":
        await _feed_ha(db_session)
        await _feed_fcon(db_session)
    else:
        await _feed_fcon(db_session)
        await _feed_ha(db_session, elveh_first=order == "fcon_then_elveh_first")
    [row] = await _trips(db_session)
    got = _snapshot(row)
    trip_id = got.pop("trip_id")
    assert got == EXPECTED
    # HA's id derives from the vehicle's own trip timestamp, whoever came first.
    from web.services.sources.ha_fordpass.adapter import compute_trip_id

    assert trip_id == compute_trip_id(DEVICE, KEYOFF)


@pytest.mark.db
async def test_rerunning_either_source_is_idempotent(db_session):
    await _feed_fcon(db_session)
    await _feed_ha(db_session)
    [before] = await _trips(db_session)
    snap = _snapshot(before)
    for p in FCON_DRIVE:
        await fcon_adapter.process_sample("telemetry", p, db_session, {"config_id": 1})
    await _feed_ha(db_session)
    [after] = await _trips(db_session)
    assert _snapshot(after) == snap


@pytest.mark.db
async def test_protected_row_is_left_alone(db_session):
    manual = await TripFactory.create(
        db_session, device_id=DEVICE, source_system="csv_import",
        start_time=T0, end_time=T0 + timedelta(minutes=25), duration=1500.0,
        distance=12.5, energy_consumed=2.5, efficiency=5.0,
    )
    await db_session.flush()
    await _feed_fcon(db_session)
    await _feed_ha(db_session)
    rows = await _trips(db_session)
    assert [r.id for r in rows] == [manual.id]
    assert _f(rows[0].distance) == 12.5 and rows[0].brake_torque is None
    assert rows[0].source_system == "csv_import"


@pytest.mark.db
async def test_different_drives_stay_separate(db_session):
    # Ended together, but began half an hour apart: not the same drive.
    await TripFactory.create(
        db_session, device_id=DEVICE, source_system=HA, trip_id=uuid.uuid4(),
        start_time=T0 - timedelta(minutes=30), end_time=T0 + timedelta(minutes=25),
        duration=3300.0, distance=40.0, energy_consumed=8.0,
    )
    await db_session.flush()
    await _feed_fcon(db_session)
    assert len(await _trips(db_session)) == 2


@pytest.mark.unit
def test_precedence_table_documents_every_field():
    assert all(rule.winner in (HA, FCON) and rule.why for rule in PRECEDENCE.values())


@pytest.mark.unit
def test_merge_fills_nulls_and_applies_winner():
    current = {"distance": 13.0, "energy_consumed": None, "cabin_temp": None, "duration": 1510.0}
    incoming = {"distance": 12.6, "energy_consumed": 2.4, "cabin_temp": 21.0, "duration": 1500.0}
    # FCON arriving onto an HA row: HA keeps distance and duration.
    changes = merge_changes({**current, "distance": 12.6}, {**incoming, "distance": 13.0}, FCON)
    assert "distance" not in changes
    assert "duration" not in changes
    assert changes["energy_consumed"] == 2.4
    assert changes["efficiency"] == 5.25


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ha_km", "stored", "warns"),
    [
        (12.6, 12.6, False),  # within the ±1 km odometer rounding: HA's decimals kept
        (13.0 * 1.609344, 13.0, True),  # km/miles twin: never stored
        (15.5, 13.0, True),  # any other disagreement
    ],
)
@pytest.mark.parametrize("fcon_first", [True, False])
def test_fcon_odometer_checks_ha_distance(ha_km, stored, warns, fcon_first, caplog):
    fcon = {"distance": 13.0, "odometer_start": 1000.0, "odometer_end": 1013.0,
            "energy_consumed": 2.6, "device_id": DEVICE}
    ha = {"distance": ha_km, "energy_consumed": 2.4, "device_id": DEVICE}
    with caplog.at_level("WARNING", logger="lightningrod.trip_merge"):
        if fcon_first:
            merged = {**fcon, **merge_changes(fcon, ha, HA)}
        else:
            merged = {**ha, **merge_changes(ha, fcon, FCON)}
    assert merged["distance"] == pytest.approx(stored)
    assert merged["efficiency"] == round(stored / 2.4, 3)
    warnings = [r for r in caplog.records if "disagreement" in r.getMessage()]
    assert bool(warnings) is warns
    if warns:
        assert f"{ha_km:.3f}" in warnings[0].getMessage()


def _twin_events() -> tuple[str, dict]:
    """HA's events trip with its km distance converted a second time."""
    entity, state = _events_state()
    seg = state["attributes"]["customEvents"]["xev-key-off-trip-segment-data"]
    trip = json.loads(seg["oemData"]["trip_data"]["stringArrayValue"][0])
    trip["distance_traveled"] = 13.0 * 1.609344
    seg["oemData"]["trip_data"]["stringArrayValue"] = [json.dumps(trip)]
    return entity, state


@pytest.mark.db
@pytest.mark.parametrize("ha_first", [True, False])
async def test_ha_twin_distance_is_never_stored(db_session, ha_first):
    async def ha():
        fp_handlers._last_trip_values.clear()
        entity, events = _twin_events()
        await ha_process(entity, events, db_session, HA_CONFIG)
        await db_session.flush()

    steps = (ha, lambda: _feed_fcon(db_session))
    for step in steps if ha_first else reversed(steps):
        await step()
    [row] = await _trips(db_session)
    assert float(row.distance) == 13.0
    assert float(row.efficiency) == round(13.0 / 2.4, 3)
