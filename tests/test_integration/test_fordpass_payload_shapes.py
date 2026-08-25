"""Locks the attribute names and value shapes ha-fordpass actually emits.

Every payload here mirrors a live Home Assistant instance running the
marq24 ha-fordpass integration: metrics attributes are `{"value": N}`
wrappers, the pack current lives on `xevBatteryIoCurrent`, elveh trip scores
end in `Score`, and the outsidetemp reading is the entity state rather than
the stale `ambientTemp` attribute.
"""

import pytest
from sqlalchemy import select

from db.models.battery_status import EVBatteryStatus
from web.services.sources.ha_fordpass.adapter import process_event

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

_DEVICE_ID = "SHAPEVIN"
_METRICS_ENTITY = f"sensor.fordpass_{_DEVICE_ID}_metrics"


def _wrap(value, update_time="2026-08-25T13:52:34Z"):
    """Shape a Ford metrics value the way the integration passes it through."""
    return {"updateTime": update_time, "oemCorrelationId": "1715", "value": value}


def _metrics_state(**metrics) -> dict:
    return {
        "entity_id": _METRICS_ENTITY,
        "state": len(metrics),
        "last_changed": "2026-08-25T13:52:34+00:00",
        "last_updated": "2026-08-25T13:52:34+00:00",
        "attributes": {
            "friendly_name": "fordpass Metrics",
            **{key: _wrap(value) for key, value in metrics.items()},
        },
    }


async def _latest_battery(db_session) -> EVBatteryStatus | None:
    return (
        await db_session.execute(
            select(EVBatteryStatus)
            .where(EVBatteryStatus.device_id == _DEVICE_ID)
            .order_by(EVBatteryStatus.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def test_metrics_value_wrappers_populate_battery_columns(db_session):
    """Every metrics attribute is a {"value": N} wrapper, not a bare scalar.

    Reading the wrapper as a number yields None, which used to write an
    all-NULL ev_battery_status row on every metrics event.
    """
    await process_event(
        _METRICS_ENTITY,
        _metrics_state(
            xevBatteryRange=343.9,
            xevBatteryMaximumRange=418.0,
            xevBatteryCapacity=131000,
            xevBatteryStateOfCharge=96.0,
            xevBatteryActualStateOfCharge=91.0,
            xevBatteryVoltage=390.0,
        ),
        db_session,
        {"unit_system": "metric"},
    )
    await db_session.flush()

    battery = await _latest_battery(db_session)
    assert battery is not None, "metrics event wrote no ev_battery_status row"
    assert float(battery.hv_battery_range) == pytest.approx(343.9)
    assert float(battery.hv_battery_max_range) == pytest.approx(418.0)
    assert float(battery.hv_battery_capacity) == pytest.approx(131.0)
    assert float(battery.hv_battery_soc) == pytest.approx(96.0)
    assert float(battery.hv_battery_actual_soc) == pytest.approx(91.0)
    assert float(battery.hv_battery_voltage) == pytest.approx(390.0)


async def test_metrics_event_without_battery_values_writes_no_row(db_session):
    """A metrics event carrying no battery keys must not insert an empty row."""
    await process_event(
        _METRICS_ENTITY,
        _metrics_state(odometer=42000.0, speed=0.0),
        db_session,
        {"unit_system": "metric"},
    )
    await db_session.flush()

    assert await _latest_battery(db_session) is None, (
        "metrics event with no battery values inserted an all-NULL row"
    )


async def test_metrics_regen_backfill_reads_the_value_wrapper(db_session):
    """Trip regen + driving score backfill also reads through the wrapper."""
    import uuid
    from datetime import UTC, datetime

    from db.models.trip_metrics import EVTripMetrics

    db_session.add(
        EVTripMetrics(
            trip_id=uuid.uuid4(),
            device_id=_DEVICE_ID,
            end_time=datetime(2026, 8, 25, 13, 0, tzinfo=UTC),
            recorded_at=datetime(2026, 8, 25, 13, 0, tzinfo=UTC),
            distance=19.0,
            source_system="ha_fordpass",
        )
    )
    await db_session.flush()

    await process_event(
        _METRICS_ENTITY,
        _metrics_state(
            xevBatteryRange=343.9,
            tripXevBatteryRangeRegenerated=2.1,
            tripXevBatteryChargeRegenerated=85,
        ),
        db_session,
        {"unit_system": "metric"},
    )
    await db_session.flush()

    trip = (
        await db_session.execute(
            select(EVTripMetrics)
            .where(EVTripMetrics.device_id == _DEVICE_ID)
            .order_by(EVTripMetrics.id.desc())
            .limit(1)
        )
    ).scalar_one()
    assert float(trip.range_regenerated) == pytest.approx(2.1)
    assert float(trip.driving_score) == pytest.approx(85.0)


async def test_pack_current_comes_from_xev_battery_io_current(db_session):
    """The pack current metric is `xevBatteryIoCurrent`.

    `xevBatteryAmperage` and `xevBatteryPower` appear nowhere in Ford's
    payload or in the integration, so pack power is derived from voltage x
    current exactly as ha-fordpass derives its elveh `batterykW` attribute.
    """
    await process_event(
        _METRICS_ENTITY,
        _metrics_state(xevBatteryVoltage=390.0, xevBatteryIoCurrent=-52.4),
        db_session,
        {"unit_system": "metric"},
    )
    await db_session.flush()

    battery = await _latest_battery(db_session)
    assert battery is not None
    assert float(battery.hv_battery_amperage) == pytest.approx(-52.4)
    assert float(battery.hv_battery_kw) == pytest.approx(-20.44, abs=0.01)


async def test_invented_amperage_and_power_keys_are_ignored(db_session):
    """A payload carrying only the fictional keys must produce no row."""
    await process_event(
        _METRICS_ENTITY,
        _metrics_state(xevBatteryAmperage=5.0, xevBatteryPower=1950),
        db_session,
        {"unit_system": "metric"},
    )
    await db_session.flush()

    assert await _latest_battery(db_session) is None
