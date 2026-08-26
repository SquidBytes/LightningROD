"""Backfilled charging sessions are stamped with today's temperatures.

`handle_energy_transfer` reads the ambient and battery temperature from two
module-level "most recent value" caches in the adapter, which the outsidetemp
and elvehcharging handlers fill from live events. That is right for a live
session, where the cached reading is a minute old.

`backfill_history` replays historical `energytransferlogentry` states through
the same handler. Nothing resets or bypasses the caches for a replay, and the
real payload carries no thermal fields of its own — `get_energy_transfer_log_attrs`
in ha-fordpass copies Ford's log entry verbatim and adds no temperatures — so
a session backfilled from three weeks ago is recorded with the temperature it
is outside right now.

The xfail below documents the defect; the live test beside it is the control
showing the cache is the correct source when the event really is current.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from tests.conftest import FixedSessionFactory

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

# 30 degC outside and a 28 degC pack: what the live handlers cached moments ago.
NOW_AMBIENT_C = 30.0
NOW_BATTERY_C = 28.0


def _session_state(entity_id: str, when: datetime) -> dict:
    """An energytransferlogentry payload shaped as ha-fordpass emits it.

    Ford's log entry carries no `outsidetemp` and no `batteryTemperature`;
    the entity's whole attribute set is that entry minus `id`/`deviceId`.
    """
    return {
        "entity_id": entity_id,
        "state": "complete",
        "last_changed": when.isoformat(),
        "last_updated": when.isoformat(),
        "attributes": {
            "energyConsumed": 41.0,
            "chargerType": "AC_BASIC",
            "timeStamp": when.isoformat(),
            "energyTransferDuration": {
                "begin": when.isoformat(),
                "end": (when + timedelta(hours=2)).isoformat(),
                "totalTime": 7200,
            },
            "plugDetails": {"totalPluggedInTime": 7500, "totalDistanceAdded": 103},
            "stateOfCharge": {"firstSOC": 20.0, "lastSOC": 90.0},
        },
    }


@pytest.fixture
def live_thermal_cache(monkeypatch):
    """Prime the adapter caches the way a live outsidetemp/elvehcharging pair would."""
    from web.services.sources.ha_fordpass import adapter as fp_adapter

    def _prime(device_id: str) -> None:
        fp_adapter._last_outsidetemp[device_id] = NOW_AMBIENT_C
        fp_adapter._last_charging_battery_temp[device_id] = NOW_BATTERY_C

    yield _prime
    fp_adapter._last_outsidetemp.clear()
    fp_adapter._last_charging_battery_temp.clear()


async def _backfilled_session(db_session, monkeypatch, vin: str, when: datetime):
    """Run `backfill_history` over a single historical session state."""
    import db.engine as db_engine
    import web.services.ingestion.raw_archive as archive_module
    from web.services.ingestion.ha_websocket import HAWebSocketRuntime

    # Backfill drives the same fan-out as a live event, so the raw archive
    # runs too. It holds its own module-level session factory; without this
    # its rows commit outside the fixture's transaction and survive the
    # rollback into whatever test runs next.
    factory = FixedSessionFactory(db_session)
    monkeypatch.setattr(db_engine, "AsyncSessionLocal", factory)
    monkeypatch.setattr(archive_module, "AsyncSessionLocal", factory)

    entity_id = f"sensor.fordpass_{vin}_energytransferlogentry"
    state = _session_state(entity_id, when)

    runtime = HAWebSocketRuntime(1, "ws://unused", "token")
    runtime.detected_vin = vin
    runtime._ha_config = {"unit_system": {"length": "km", "temperature": "°C"}}

    async def _history(requested_entity_id, _start_iso):
        return [state] if requested_entity_id == entity_id else []

    async def _no_gas_sensors():
        return []

    monkeypatch.setattr(runtime, "_fetch_entity_history", _history)
    monkeypatch.setattr(runtime, "_load_gas_sensor_entity_ids", _no_gas_sensors)

    result = await runtime.backfill_history(days=30)
    assert result["sessions"]["processed"] == 1, result
    await db_session.flush()

    return (
        await db_session.execute(
            select(EVChargingSession).where(EVChargingSession.device_id == vin)
        )
    ).scalar_one()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "backfill_history replays historical sessions through the live "
        "thermal caches, so a three-week-old session is recorded with "
        "today's ambient and battery temperature"
    ),
)
async def test_backfilled_session_is_not_given_todays_temperature(
    db_session, monkeypatch, live_thermal_cache
):
    """A session replayed from three weeks ago has no thermal reading to record.

    The correct value is None: neither the record nor anything scoped to it
    says how warm it was that day. Anything else is this moment's weather
    written into a three-week-old row, indistinguishable afterwards from a
    real measurement.
    """
    vin = "BACKFILLVIN"
    three_weeks_ago = datetime.now(UTC) - timedelta(days=21)
    live_thermal_cache(vin)

    session = await _backfilled_session(db_session, monkeypatch, vin, three_weeks_ago)

    age_days = (datetime.now(UTC) - session.session_start_utc.replace(tzinfo=UTC)).days
    assert age_days >= 20, "the fixture did not actually backfill a historical session"
    assert session.ambient_temp_start is None, (
        f"session from {age_days} days ago recorded "
        f"{session.ambient_temp_start} degC ambient — today's cached reading"
    )
    assert session.battery_temp_start is None, (
        f"session from {age_days} days ago recorded "
        f"{session.battery_temp_start} degC pack — today's cached reading"
    )


async def test_live_session_does_take_the_cached_temperature(
    db_session, monkeypatch, live_thermal_cache
):
    """The control: for an event that really is current, the cache is right.

    Without this side, the xfail above would read as "the cache is never the
    right source", which is not the defect.
    """
    from web.services.sources.ha_fordpass.handlers import handle_energy_transfer

    vin = "LIVEVIN"
    now = datetime.now(UTC)
    live_thermal_cache(vin)

    await handle_energy_transfer(
        "energytransferlogentry",
        _session_state(f"sensor.fordpass_{vin}_energytransferlogentry", now),
        {"unit_system": {"length": "km", "temperature": "°C"}},
        vin,
        db_session,
    )
    await db_session.flush()

    session = (
        await db_session.execute(
            select(EVChargingSession).where(EVChargingSession.device_id == vin)
        )
    ).scalar_one()
    assert float(session.ambient_temp_start) == pytest.approx(NOW_AMBIENT_C)
    assert float(session.battery_temp_start) == pytest.approx(NOW_BATTERY_C)
