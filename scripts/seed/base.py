"""Shared seeding facilities: time-shift helper, contract-driven generator, gap reporter."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus

# Import the model classes we'll query for MAX timestamps.
from db.models.charging_session import EVChargingSession
from db.models.location import EVLocation
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle_status import EVVehicleStatus

# (table, timestamp_column_attr) tuples that define the universe of "demo data time"
# Note: EVTripMetrics uses `end_time` (not `trip_end_utc` — that column does not exist)
TIMESTAMP_SOURCES: list[tuple[type, str]] = [
    (EVChargingSession, "session_end_utc"),
    (EVBatteryStatus, "recorded_at"),
    (EVTripMetrics, "end_time"),
    (EVVehicleStatus, "recorded_at"),
    (EVLocation, "recorded_at"),
]


@dataclass(frozen=True)
class OffsetResult:
    offset: timedelta
    max_observed: datetime | None
    now: datetime

    @property
    def has_data(self) -> bool:
        return self.max_observed is not None


async def compute_global_offset(
    db: AsyncSession,
    *,
    sources: list[tuple[type, str]] | None = None,
    now: datetime | None = None,
) -> OffsetResult:
    """Compute a single global time offset = now - max(timestamp across all sources).

    Used to shift ALL demo timestamps uniformly so the most recent row is "now"
    while preserving relative spacing between rows. If no rows exist, offset is
    timedelta(0) and max_observed is None.
    """
    sources = TIMESTAMP_SOURCES if sources is None else sources
    now = now or datetime.now(UTC)

    max_observed: datetime | None = None
    for model, ts_attr in sources:
        col = getattr(model, ts_attr)
        result = await db.execute(select(func.max(col)))
        row_max = result.scalar()
        if row_max is None:
            continue
        # Normalize to UTC-aware
        if row_max.tzinfo is None:
            row_max = row_max.replace(tzinfo=UTC)
        if max_observed is None or row_max > max_observed:
            max_observed = row_max

    if max_observed is None:
        return OffsetResult(offset=timedelta(0), max_observed=None, now=now)
    return OffsetResult(offset=now - max_observed, max_observed=max_observed, now=now)


def shift_datetime(value: datetime | None, offset: timedelta) -> datetime | None:
    """Apply offset to a single datetime; returns None unchanged."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value + offset


async def apply_offset_to_table(
    db: AsyncSession,
    model: type,
    ts_columns: Iterable[str],
    offset: timedelta,
) -> int:
    """Bulk-shift timestamp columns on every row of a table. Returns rows updated.

    Uses an UPDATE statement with column = column + offset (PostgreSQL interval).
    Only call after generation; idempotent shift = noop only if offset is zero.
    """
    if offset == timedelta(0):
        return 0
    from sqlalchemy import update
    rows_updated = 0
    for ts_col_name in ts_columns:
        col = getattr(model, ts_col_name)
        stmt = update(model).values({ts_col_name: col + offset}).where(col.isnot(None))
        result = await db.execute(stmt)
        rows_updated = max(rows_updated, result.rowcount or 0)
    return rows_updated
