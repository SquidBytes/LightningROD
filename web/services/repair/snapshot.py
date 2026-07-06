"""Row snapshot serialization plus run listing, restore, and purge."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric, Uuid, delete, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics

# v1 restore scope: only these tables can be restored from snapshots.
RESTORABLE_MODELS: dict[str, type[EVTripMetrics]] = {"ev_trip_metrics": EVTripMetrics}


def serialize_row(obj: Any) -> dict[str, Any]:
    """JSON-safe dict of an ORM row's column values."""
    out: dict[str, Any] = {}
    for col in inspect(obj).mapper.columns:
        val = getattr(obj, col.key)
        if isinstance(val, Decimal):
            val = float(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, uuid.UUID):
            val = str(val)
        out[col.key] = val
    return out


def deserialize_row(model: type, data: dict[str, Any]) -> dict[str, Any]:
    """Coerce a serialized row dict back to the model's column Python types."""
    columns: Any = inspect(model).columns
    out: dict[str, Any] = {}
    for key, val in data.items():
        if key not in columns or val is None:
            out[key] = val
            continue
        col_type = columns[key].type
        if isinstance(col_type, DateTime) and isinstance(val, str):
            val = datetime.fromisoformat(val)
        elif isinstance(col_type, Uuid) and isinstance(val, str):
            val = uuid.UUID(val)
        elif isinstance(col_type, Numeric) and isinstance(val, int | float):
            val = float(val)
        out[key] = val
    return out


async def snapshot_rows(
    db: AsyncSession, run_id: uuid.UUID, operation: str, rows: list[Any]
) -> int:
    """Insert one RepairBackup per ORM row under run_id; return count snapshotted."""
    for row in rows:
        db.add(
            RepairBackup(
                run_id=run_id,
                operation=operation,
                table_name=row.__tablename__,
                row_pk=row.id,
                row_json=serialize_row(row),
            )
        )
    await db.flush()
    return len(rows)


async def list_runs(db: AsyncSession) -> list[dict[str, Any]]:
    """Snapshot run summaries (run_id, operation, row count, created_at), newest first."""
    stmt = (
        select(
            RepairBackup.run_id,
            RepairBackup.operation,
            func.count(RepairBackup.id).label("row_count"),
            func.min(RepairBackup.created_at).label("created_at"),
        )
        .group_by(RepairBackup.run_id, RepairBackup.operation)
        .order_by(func.min(RepairBackup.created_at).desc())
    )
    result = await db.execute(stmt)
    return [
        {
            "run_id": r.run_id,
            "operation": r.operation,
            "row_count": r.row_count,
            "created_at": r.created_at,
        }
        for r in result
    ]


async def restore_run(db: AsyncSession, run_id: uuid.UUID) -> int:
    """Write a run's snapshotted rows back: update surviving pks, re-insert deleted ones."""
    backups = (
        (await db.execute(select(RepairBackup).where(RepairBackup.run_id == run_id)))
        .scalars()
        .all()
    )
    restored = 0
    for backup in backups:
        model = RESTORABLE_MODELS.get(backup.table_name)
        if model is None:
            # Unknown table (schema drift or newer snapshot) — skip, never guess.
            continue
        data = deserialize_row(model, backup.row_json)
        row = await db.get(model, backup.row_pk)
        if row is None:
            db.add(model(**data))  # re-insert with the original pk
        else:
            for key, val in data.items():
                setattr(row, key, val)
        restored += 1
    await db.flush()
    return restored


async def purge_run(db: AsyncSession, run_id: uuid.UUID) -> int:
    """Delete every snapshot row for a run; return count removed."""
    result = await db.execute(
        delete(RepairBackup).where(RepairBackup.run_id == run_id)
    )
    return int(getattr(result, "rowcount", 0) or 0)
