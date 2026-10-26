"""Upsert parsed metric rows, keyed on (device_id, recorded_at, source_system)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle_status import EVVehicleStatus
from web.services.sources.ford_metrics.parser import BATTERY, VEHICLE, ParsedRow

INGEST_SCHEMA_VERSION = 2
_MODELS: dict[str, Any] = {BATTERY: EVBatteryStatus, VEHICLE: EVVehicleStatus}


async def _clamp_odometer(
    db: AsyncSession, device_id: str, at: datetime, odometer: float
) -> float:
    """Never let the odometer run backwards past an earlier reading."""
    previous = await db.scalar(
        select(func.max(EVVehicleStatus.odometer)).where(
            EVVehicleStatus.device_id == device_id,
            EVVehicleStatus.recorded_at < at,
        )
    )
    return max(odometer, float(previous)) if previous is not None else odometer


async def upsert_rows(
    db: AsyncSession, device_id: str, rows: list[ParsedRow], *, source_system: str
) -> int:
    """Insert or refresh each row; re-parsing the same payload is a no-op."""
    for row in rows:
        model = _MODELS[row.table]
        values = dict(row.values)
        if row.table == VEHICLE and values.get("odometer") is not None:
            values["odometer"] = await _clamp_odometer(
                db, device_id, row.recorded_at, values["odometer"]
            )
        if hasattr(model, "ingest_schema_version"):
            values["ingest_schema_version"] = INGEST_SCHEMA_VERSION
        existing = await db.scalar(
            select(model).where(
                model.device_id == device_id,
                model.recorded_at == row.recorded_at,
                model.source_system == source_system,
            )
        )
        if existing is None:
            db.add(
                model(
                    device_id=device_id,
                    recorded_at=row.recorded_at,
                    source_system=source_system,
                    original_timestamp=row.recorded_at,
                    **values,
                )
            )
        else:
            for column, value in values.items():
                setattr(existing, column, value)
    await db.flush()
    return len(rows)
