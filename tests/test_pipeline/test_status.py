"""upsert_status: one battery or vehicle row per (device, reading time, source)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle_status import EVVehicleStatus
from web.services.pipeline.observation import Observation
from web.services.pipeline.status import upsert_status
from web.util.timestamps import utc

pytestmark = pytest.mark.db

DEVICE = "your_vin_000000001"
AT = datetime(2026, 8, 24, 17, 0, tzinfo=UTC)
BATTERY = "ev_battery_status"
VEHICLE = "ev_vehicle_status"


def _obs(table: str, values: dict, *, at: datetime = AT, source: str = "fcon") -> Observation:
    return Observation(DEVICE, table, at, values, source)


async def _rows(db, model):
    return (
        await db.execute(select(model).where(model.device_id == DEVICE).order_by(model.id))
    ).scalars().all()


async def test_later_observation_fills_columns_and_keeps_the_rest(db_session):
    await upsert_status(db_session, [_obs(BATTERY, {"hv_battery_soc": 76.5, "hv_battery_range": 250.0})])
    await upsert_status(db_session, [_obs(BATTERY, {"hv_battery_range": 300.0, "lv_battery_voltage": 12.4})])

    (row,) = await _rows(db_session, EVBatteryStatus)
    assert row.hv_battery_soc == 76.5
    assert row.hv_battery_range == 300.0
    assert row.lv_battery_voltage == 12.4


async def test_row_is_stamped_with_the_reading_time_and_source(db_session):
    await upsert_status(db_session, [_obs(BATTERY, {"hv_battery_soc": 50.0}, source="ha_fordpass")])

    (row,) = await _rows(db_session, EVBatteryStatus)
    assert utc(row.recorded_at) == AT
    assert utc(row.original_timestamp) == AT
    assert row.source_system == "ha_fordpass"
    assert row.ingest_schema_version == 2


async def test_two_sources_at_the_same_instant_keep_separate_rows(db_session):
    await upsert_status(db_session, [_obs(BATTERY, {"hv_battery_soc": 76.5})])
    await upsert_status(db_session, [_obs(BATTERY, {"hv_battery_soc": 76.0}, source="ha_fordpass")])

    assert [r.source_system for r in await _rows(db_session, EVBatteryStatus)] == ["fcon", "ha_fordpass"]


async def test_odometer_never_runs_backwards(db_session):
    await upsert_status(db_session, [_obs(VEHICLE, {"odometer": 30600.0})])
    later = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
    await upsert_status(db_session, [_obs(VEHICLE, {"odometer": 30500.0}, at=later)])

    assert [r.odometer for r in await _rows(db_session, EVVehicleStatus)] == [30600.0, 30600.0]
