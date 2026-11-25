"""Battery and vehicle status rows, one per (device_id, recorded_at, source_system)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle_status import EVVehicleStatus
from web.services.pipeline.observation import INGEST_SCHEMA_VERSION, Observation

BATTERY = EVBatteryStatus.__tablename__
VEHICLE = EVVehicleStatus.__tablename__
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


async def upsert_status(db: AsyncSession, observations: Iterable[Observation]) -> int:
    """Insert each row, or set only the observed columns on the one already there."""
    count = 0
    for obs in observations:
        model = _MODELS[obs.table]
        values = dict(obs.values)
        if obs.table == VEHICLE and values.get("odometer") is not None:
            values["odometer"] = await _clamp_odometer(
                db, obs.device_id, obs.recorded_at, values["odometer"]
            )
        version = INGEST_SCHEMA_VERSION.get(obs.source_system)
        if version is not None and hasattr(model, "ingest_schema_version"):
            values["ingest_schema_version"] = version
        existing = await db.scalar(
            select(model).where(
                model.device_id == obs.device_id,
                model.recorded_at == obs.recorded_at,
                model.source_system == obs.source_system,
            )
        )
        if existing is None:
            db.add(
                model(
                    device_id=obs.device_id,
                    recorded_at=obs.recorded_at,
                    source_system=obs.source_system,
                    original_timestamp=obs.recorded_at,
                    **values,
                )
            )
        else:
            for column, value in values.items():
                setattr(existing, column, value)
        count += 1
    await db.flush()
    return count
