"""One charging-session row per charge when Home Assistant and FordConnect both record it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from tests.test_fcon.payloads import telemetry
from web.services.session_merge import HA, KEEP, SESSION_PRECEDENCE
from web.services.sources.fcon import adapter as fcon_adapter
from web.services.sources.fcon.raw_store import FconRawArchive
from web.services.sources.ha_fordpass.handlers import handle_energy_transfer

pytestmark = pytest.mark.db

VIN = "YOURVIN0000000002"
DEVICE = VIN.lower()
T0 = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)
ACTUAL = {"location": {"lat": 10.0, "lon": 20.0}, "gpsDimension": "DIM_3D", "gpsCoordinateMethod": "ACTUAL"}


def _fcon(minutes: int, plug: str, display: str, **values) -> dict:
    stamp = (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    charging = display == "IN_PROGRESS"
    p = telemetry(
        stamp,
        xevPlugChargerStatus=plug,
        xevBatteryChargeDisplayStatus=display,
        xevBatteryChargerVoltageOutput=355.0 if charging else 2.0,
        xevBatteryChargerCurrentOutput=20.9 if charging else 0.0,
        **values,
    )
    p["vin"] = VIN
    return p


FCON_CHARGE = [
    _fcon(-10, "DISCONNECTED", "NOT_READY"),
    _fcon(0, "CONNECTED", "IN_PROGRESS", xevBatteryEnergyRemaining=50.0,
          xevBatteryStateOfCharge=50.0, xevBatteryTemperature=25.0, position=ACTUAL),
    _fcon(20, "CONNECTED", "IN_PROGRESS", xevBatteryEnergyRemaining=55.0),
    _fcon(40, "DISCONNECTED", "NOT_READY", xevBatteryEnergyRemaining=55.0,
          xevBatteryStateOfCharge=55.0, xevBatteryTemperature=31.0),
]


def _ha_state() -> dict:
    begin = (T0 + timedelta(minutes=1)).isoformat()
    end = (T0 + timedelta(minutes=39)).isoformat()
    return {
        "state": "complete", "last_changed": end, "last_updated": end,
        "attributes": {
            "energyConsumed": 5.2, "chargerType": "AC_LEVEL_2",
            "energyTransferDuration": {"begin": begin, "end": end, "totalTime": 2280.0},
            "plugDetails": {"totalPluggedInTime": 2400.0, "totalDistanceAdded": 21.0},
            "stateOfCharge": {"firstSOC": 50.5, "lastSOC": 55.5},
            "power": {"max": 7600.0, "min": 6900.0, "weightedAverage": 7400.0},
            "location": {"name": "Depot", "network": "UNKNOWN", "latitude": 10.0,
                         "longitude": 20.0, "address": {"address1": "1 Test Way", "city": "Town"}},
            "timeStamp": end,
        },
    }


async def _feed_fcon(db) -> None:
    archive = FconRawArchive()
    for p in FCON_CHARGE:
        # Parse even an already-archived sample: some tests feed the charge twice.
        await archive.store(db, 1, "telemetry", p, T0)
        await fcon_adapter.process_sample("telemetry", p, db, {"config_id": 1})
    await db.flush()


async def _feed_ha(db) -> None:
    await handle_energy_transfer("energytransferlogentry", _ha_state(), {}, DEVICE, db)
    await db.flush()


async def _sessions(db) -> list[EVChargingSession]:
    rows = (await db.execute(
        select(EVChargingSession).where(EVChargingSession.device_id == DEVICE)
    )).scalars().all()
    for r in rows:
        await db.refresh(r)
    return list(rows)


def _aware(v):
    return v.replace(tzinfo=UTC) if isinstance(v, datetime) and v.tzinfo is None else v


def _snap(row: EVChargingSession) -> dict:
    out = {}
    for field in SESSION_PRECEDENCE:
        v = _aware(getattr(row, field))
        out[field] = float(v) if hasattr(v, "is_finite") else v
    return out


EXPECTED = {
    "session_start_utc": T0 + timedelta(minutes=1),
    "session_end_utc": T0 + timedelta(minutes=39),
    "charge_duration_seconds": 2280.0,
    "plugged_in_duration_seconds": 2400.0,
    "energy_kwh": 5.2,
    "start_soc": 50.5,
    "end_soc": 55.5,
    "max_power": 7.6,
    "charging_kw": 7.4,
    "charge_type": "AC",
    "address": "1 Test Way, Town",
    "battery_temp_start": 25.0,
    "battery_temp_end": 31.0,
    "charging_voltage": 355.0,
    "station_status": "NOT_DETECTED",
    "source_system": HA,
}


@pytest.mark.parametrize("ha_first", [True, False])
async def test_both_orders_give_one_identical_row(db_session, ha_first):
    for step in (_feed_ha, _feed_fcon) if ha_first else (_feed_fcon, _feed_ha):
        await step(db_session)
    [row] = await _sessions(db_session)
    snap = _snap(row)
    for field, want in EXPECTED.items():
        got = snap[field]
        assert got == (pytest.approx(want) if isinstance(want, float) else want), field
    assert row.location_id is not None
    assert not row.needs_review and row.duplicate_of_id is None


async def test_orders_agree_on_every_column(db_session):
    await _feed_ha(db_session)
    await _feed_fcon(db_session)
    [first] = await _sessions(db_session)
    a = _snap(first)
    await db_session.delete(first)
    await db_session.flush()
    await _feed_fcon(db_session)
    await _feed_ha(db_session)
    [second] = await _sessions(db_session)
    assert _snap(second) == a


async def test_curated_fields_survive_a_merge_and_a_rerun(db_session):
    await _feed_fcon(db_session)
    [row] = await _sessions(db_session)
    row.location_name = "My Garage"
    row.cost = 1.23
    row.is_free = False
    await db_session.flush()
    await _feed_ha(db_session)
    for p in FCON_CHARGE:  # a re-parse of the archive
        await fcon_adapter.process_sample("telemetry", p, db_session, {"config_id": 1})
    [row] = await _sessions(db_session)
    assert (row.location_name, float(row.cost), row.is_free) == ("My Garage", 1.23, False)


async def test_rerunning_is_idempotent(db_session):
    await _feed_fcon(db_session)
    await _feed_ha(db_session)
    [row] = await _sessions(db_session)
    before = _snap(row)
    await _feed_ha(db_session)
    for p in FCON_CHARGE:
        await fcon_adapter.process_sample("telemetry", p, db_session, {"config_id": 1})
    [row] = await _sessions(db_session)
    assert _snap(row) == before


async def test_protected_session_is_untouched_and_nothing_written(db_session):
    manual = EVChargingSession(
        device_id=DEVICE, source_system="csv_import", is_complete=True,
        session_start_utc=T0, energy_kwh=5.0, cost=2.0,
    )
    db_session.add(manual)
    await db_session.flush()
    await _feed_fcon(db_session)
    rows = await _sessions(db_session)
    assert [r.id for r in rows] == [manual.id]
    assert rows[0].source_system == "csv_import" and float(rows[0].energy_kwh) == 5.0


async def test_ha_against_protected_session_still_goes_to_review(db_session):
    manual = EVChargingSession(
        device_id=DEVICE, source_system="csv_import", is_complete=True,
        session_start_utc=T0, energy_kwh=5.0,
    )
    db_session.add(manual)
    await db_session.flush()
    await _feed_ha(db_session)
    rows = await _sessions(db_session)
    ha_row = next(r for r in rows if r.source_system == HA)
    assert ha_row.duplicate_of_id == manual.id and ha_row.needs_review


@pytest.mark.unit
def test_every_rule_documents_its_reason():
    assert all(rule.why for rule in SESSION_PRECEDENCE.values())
    assert SESSION_PRECEDENCE["cost"].winner == KEEP


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ha_km", "stored", "warns"),
    [
        (12.5, 12.5, False),  # range added vs range-estimate change: close enough, HA wins
        (13.0 * 1.609344, 13.0, True),  # HA converted km as miles
        (13.0 / 1.609344, 13.0, True),  # ...or miles as km
    ],
)
@pytest.mark.parametrize("fcon_first", [True, False])
def test_distance_added_rejects_unit_conversion(ha_km, stored, warns, fcon_first, caplog):
    from web.services.session_merge import FCON, merge_values

    fcon = {"distance_added": 13.0, "device_id": DEVICE}
    ha = {"distance_added": ha_km, "device_id": DEVICE}
    with caplog.at_level("WARNING", logger="lightningrod.session_merge"):
        if fcon_first:
            merged = {**fcon, **merge_values(SESSION_PRECEDENCE, fcon, ha, HA)}
        else:
            merged = {**ha, **merge_values(SESSION_PRECEDENCE, ha, fcon, FCON)}
    assert merged["distance_added"] == pytest.approx(stored)
    hits = [r for r in caplog.records if "unit-converted" in r.getMessage()]
    assert bool(hits) is warns
    if warns:
        assert f"{ha_km:.3f}" in hits[0].getMessage()


@pytest.mark.parametrize("ha_first", [True, False])
async def test_converted_distance_added_never_stored(db_session, ha_first):
    # FordConnect sees 13 km of range added; HA reports it converted a second time.
    FCON_CHARGE[1]["metrics"]["xevBatteryRange"]["value"] = 200.0
    FCON_CHARGE[3]["metrics"]["xevBatteryRange"]["value"] = 213.0
    state = _ha_state()
    state["attributes"]["plugDetails"]["totalDistanceAdded"] = 13.0 * 1.609344

    async def ha(db):
        await handle_energy_transfer("energytransferlogentry", state, {}, DEVICE, db)
        await db.flush()

    try:
        for step in (ha, _feed_fcon) if ha_first else (_feed_fcon, ha):
            await step(db_session)
    finally:
        for sample in (FCON_CHARGE[1], FCON_CHARGE[3]):
            sample["metrics"]["xevBatteryRange"]["value"] = 272.5
    [row] = await _sessions(db_session)
    assert float(row.distance_added) == 13.0
