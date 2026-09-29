"""Charge sessions from FordConnect telemetry: plug-span boundaries and fields."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from tests.test_fcon.payloads import telemetry
from web.services.sources.fcon import adapter
from web.services.sources.fcon.charge_sessions import (
    ChargeSample,
    derive_session,
    schedule_location_name,
)
from web.services.sources.fcon.raw_store import FconRawArchive

T0 = datetime(2026, 8, 24, 20, 0, tzinfo=UTC)
DEVICE = "your_vin_000000001"
ACTUAL = {"location": {"lat": 10.0, "lon": 20.0}, "gpsDimension": "DIM_3D", "gpsCoordinateMethod": "ACTUAL"}
INFERRED = {"location": {"lat": 11.0, "lon": 21.0}, "gpsDimension": "NONE", "gpsCoordinateMethod": "INFERRED"}


def _stamp(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload(minutes: int, plug: str, display: str, **values) -> dict:
    base = {
        "xevPlugChargerStatus": plug,
        "xevBatteryChargeDisplayStatus": display,
        "xevBatteryChargerVoltageOutput": 355.0 if display == "IN_PROGRESS" else 2.0,
        "xevBatteryChargerCurrentOutput": 20.9 if display == "IN_PROGRESS" else 0.0,
    }
    base.update(values)
    return telemetry(_stamp(minutes), **base)


def _sample(minutes: int, plug: str, display: str, **values) -> ChargeSample:
    s = ChargeSample.from_payload(_payload(minutes, plug, display, **values))
    assert s is not None
    return s


def _charge(**extra) -> list[ChargeSample]:
    return [
        _sample(0, "CONNECTED", "IN_PROGRESS", xevBatteryStateOfCharge=50.0,
                xevBatteryEnergyRemaining=50.0, xevBatteryRange=200.0, position=INFERRED),
        _sample(10, "CONNECTED", "IN_PROGRESS", xevBatteryEnergyRemaining=51.5, position=ACTUAL),
        _sample(20, "CONNECTED", "PAUSED", xevBatteryEnergyRemaining=51.2),
        _sample(30, "CONNECTED", "IN_PROGRESS", xevBatteryEnergyRemaining=51.4),
        _sample(40, "DISCONNECTED", "NOT_READY", xevBatteryStateOfCharge=52.0,
                xevBatteryEnergyRemaining=51.3, xevBatteryRange=208.5),
    ]


@pytest.mark.unit
def test_pause_neither_splits_nor_accrues():
    f = derive_session(_charge())
    assert f is not None
    assert f["session_start_utc"] == T0
    assert f["session_end_utc"] == T0 + timedelta(minutes=40)
    assert f["charge_duration_seconds"] == 1800
    assert f["plugged_in_duration_seconds"] == 2400
    assert (f["start_soc"], f["end_soc"]) == (50.0, 52.0)
    assert f["distance_added"] == 8.5


@pytest.mark.unit
def test_energy_is_clamped_endpoint_delta_not_summed_deltas():
    # Summing positive deltas would give 1.5 + 0.2 = 1.7.
    assert derive_session(_charge())["energy_kwh"] == 1.5


@pytest.mark.unit
def test_only_measured_fix_is_used():
    f = derive_session(_charge())
    assert (f["latitude"], f["longitude"]) == (10.0, 20.0)
    inferred_only = [
        _sample(0, "CONNECTED", "IN_PROGRESS", position=INFERRED),
        _sample(5, "DISCONNECTED", "NOT_READY", position=INFERRED),
    ]
    assert derive_session(inferred_only)["latitude"] is None


@pytest.mark.unit
def test_plugged_but_never_charged_is_no_session():
    samples = [
        _sample(0, "CONNECTED", "SCHEDULED"),
        _sample(30, "DISCONNECTED", "NOT_READY"),
    ]
    assert derive_session(samples) is None


@pytest.mark.unit
def test_session_starts_at_first_charging_sample():
    samples = [_sample(0, "CONNECTED", "SCHEDULED")] + _charge()[0:1] + [
        _sample(10, "DISCONNECTED", "NOT_READY"),
    ]
    assert derive_session(samples)["session_start_utc"] == T0


@pytest.mark.unit
def test_charging_plug_value_also_starts():
    assert _sample(0, "CHARGINGAC", "NOT_READY").charging


@pytest.mark.unit
def test_power_with_idle_guard_and_dc_fallback():
    ac = _sample(0, "CONNECTED", "IN_PROGRESS")
    assert ac.kw == round(355.0 * 20.9 / 1000, 3)
    idle = _sample(0, "DISCONNECTED", "NOT_READY")
    assert idle.kw is None and idle.volts is None
    dc = _sample(0, "CONNECTED", "IN_PROGRESS", xevBatteryChargerCurrentOutput=0.0,
                 xevBatteryIoCurrent=-100.0, xevBatteryChargerVoltageOutput=400.0)
    assert dc.kw == 40.0
    f = derive_session(_charge())
    assert f["max_power"] == f["min_power"] == ac.kw


@pytest.mark.unit
def test_estimated_end_from_time_to_full():
    s = _sample(0, "CONNECTED", "IN_PROGRESS", xevBatteryTimeToFullCharge=90.0)
    assert s.estimated_end == T0 + timedelta(minutes=90)


@pytest.mark.unit
def test_schedule_location_name_within_radius():
    schedules = {"chargeSchedules": [
        {"location": {"name": "Work", "latitude": 10.0005, "longitude": 20.0}},
        {"location": {"name": "Far", "latitude": 30.0, "longitude": 20.0}},
    ]}
    assert schedule_location_name(schedules, 10.0, 20.0) == "Work"
    assert schedule_location_name(schedules, 30.5, 20.0) is None
    assert schedule_location_name(None, 10.0, 20.0) is None


async def _feed(db, payloads: list[dict], schedules: dict | None = None):
    """Archive then parse, as the ingest path does."""
    archive = FconRawArchive()
    if schedules is not None:
        await archive.store(db, 1, "charge-schedules", schedules, T0)
    for p in payloads:
        if await archive.store(db, 1, "telemetry", p, T0):
            await adapter.process_sample("telemetry", p, db, {"config_id": 1})
    await db.commit()


def _charge_payloads() -> list[dict]:
    return [
        _payload(-10, "DISCONNECTED", "NOT_READY"),
        _payload(0, "CONNECTED", "IN_PROGRESS", xevBatteryEnergyRemaining=50.0, position=ACTUAL),
        _payload(20, "CONNECTED", "IN_PROGRESS", xevBatteryEnergyRemaining=55.0),
        _payload(40, "DISCONNECTED", "NOT_READY", xevBatteryEnergyRemaining=54.9),
        _payload(60, "DISCONNECTED", "NOT_READY"),
    ]


@pytest.mark.db
async def test_unplug_writes_one_session_with_schedule_name(db_session):
    schedules = {"chargeSchedules": [{"location": {"name": "Depot", "latitude": 10.0, "longitude": 20.0}}]}
    await _feed(db_session, _charge_payloads(), schedules)
    sessions = (await db_session.execute(
        select(EVChargingSession).where(EVChargingSession.device_id == DEVICE)
    )).scalars().all()
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source_system == "fcon"
    assert s.is_complete
    assert float(s.energy_kwh) == 5.0
    assert float(s.charge_duration_seconds) == 2400
    assert s.location_name == "Depot"
    assert s.location_id is not None


@pytest.mark.db
async def test_reparse_does_not_duplicate(db_session):
    await _feed(db_session, _charge_payloads())
    # Parse the unplug again, as a re-parse after a mapping fix would.
    await adapter.process_sample("telemetry", _charge_payloads()[3], db_session, {"config_id": 1})
    await db_session.commit()
    count = len((await db_session.execute(
        select(EVChargingSession.id).where(EVChargingSession.device_id == DEVICE)
    )).all())
    assert count == 1
