"""FordConnect adapter writing parsed rows: idempotent upsert, clamp, vehicles."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle import EVVehicle
from db.models.vehicle_status import EVVehicleStatus
from tests.test_fcon.payloads import telemetry
from web.services.sources.fcon import adapter

pytestmark = pytest.mark.db

DEVICE = "your_vin_000000001"


async def _count(db, model) -> int:
    return await db.scalar(
        select(func.count()).select_from(model).where(model.device_id == DEVICE)
    )


async def test_telemetry_writes_rows_once(db_session):
    await adapter.process_sample("telemetry", telemetry(), db_session)
    await adapter.process_sample("telemetry", telemetry(), db_session)
    await db_session.commit()
    assert await _count(db_session, EVBatteryStatus) == 1
    assert await _count(db_session, EVVehicleStatus) == 1
    battery = await db_session.scalar(select(EVBatteryStatus).where(EVBatteryStatus.device_id == DEVICE))
    assert battery.source_system == "fcon"
    assert float(battery.hv_battery_soc) == 76.5
    assert battery.lv_load_status == "NORMAL_OPERATION"
    assert battery.ingest_schema_version == 2


async def test_reparse_updates_the_same_row(db_session):
    await adapter.process_sample("telemetry", telemetry(), db_session)
    fixed = telemetry()
    fixed["metrics"]["xevBatteryRange"]["value"] = 300.0
    await adapter.process_sample("telemetry", fixed, db_session)
    await db_session.commit()
    assert await _count(db_session, EVBatteryStatus) == 1
    battery = await db_session.scalar(select(EVBatteryStatus).where(EVBatteryStatus.device_id == DEVICE))
    assert float(battery.hv_battery_range) == 300.0


async def test_new_reading_new_row_and_odometer_clamped(db_session):
    await adapter.process_sample("telemetry", telemetry(), db_session)
    later = telemetry("2026-08-24T18:00:00Z", odometer=30500.0)
    await adapter.process_sample("telemetry", later, db_session)
    await db_session.commit()
    odos = (
        await db_session.execute(
            select(EVVehicleStatus.odometer)
            .where(EVVehicleStatus.device_id == DEVICE)
            .order_by(EVVehicleStatus.recorded_at)
        )
    ).scalars().all()
    assert [float(o) for o in odos] == [30549.0, 30549.0]


async def test_garage_names_the_vehicle(db_session):
    await adapter.process_sample(
        "garage",
        {"vin": "YOUR_VIN_000000001", "nickName": "Truck", "modelName": "F-150", "modelYear": "2023"},
        db_session,
    )
    await adapter.process_sample("telemetry", telemetry(), db_session)
    await db_session.commit()
    vehicle = await db_session.scalar(select(EVVehicle).where(EVVehicle.device_id == DEVICE))
    assert vehicle.display_name == "Truck"
    assert vehicle.source_system == "fcon"


async def test_last_seen_feeds_admin_page(db_session):
    adapter._last_seen_raw.clear()
    await adapter.process_sample("telemetry", telemetry(), db_session)
    key = next(m.key for m in adapter.DATA_MAP if m.source_field == "xevBatteryRange")
    assert adapter._last_seen_raw[key]["value"] == 272.5
    assert key.startswith("http_endpoint|telemetry|")


async def test_recorded_at_round_trips(db_session):
    await adapter.process_sample("telemetry", telemetry(), db_session)
    await db_session.commit()
    row = await db_session.scalar(select(EVVehicleStatus).where(EVVehicleStatus.device_id == DEVICE))
    ts = row.recorded_at if row.recorded_at.tzinfo else row.recorded_at.replace(tzinfo=UTC)
    assert ts == datetime(2026, 8, 24, 16, 59, 31, tzinfo=UTC)
