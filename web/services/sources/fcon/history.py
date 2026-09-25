"""Read recent telemetry back from the raw archive, newest first until a boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.fcon_raw_sample import FconRawSample

# How far back an ending sample looks for the start it closes.
MAX_LOOKBACK = timedelta(days=14)
_BATCH = 200


async def telemetry_since_boundary(
    db: AsyncSession,
    device_id: str,
    until: datetime,
    is_boundary: Callable[[dict], bool],
) -> list[dict]:
    """Payloads after the previous boundary sample, up to and including `until`, oldest first.

    The sample at `until` is included even when it is itself a boundary; the
    walk stops at the next boundary before it.
    """
    collected: list[dict] = []
    floor = until - MAX_LOOKBACK
    cursor = until
    include_cursor = True
    while True:
        bound = (
            FconRawSample.recorded_at <= cursor
            if include_cursor
            else FconRawSample.recorded_at < cursor
        )
        stmt = (
            select(FconRawSample.payload, FconRawSample.recorded_at)
            .where(
                FconRawSample.device_id == device_id,
                FconRawSample.endpoint == "telemetry",
                FconRawSample.recorded_at >= floor,
                bound,
            )
            .order_by(FconRawSample.recorded_at.desc())
            .limit(_BATCH)
        )
        rows = (await db.execute(stmt)).all()
        for payload, recorded_at in rows:
            if collected and is_boundary(payload):
                return list(reversed(collected))
            collected.append(payload)
            cursor = recorded_at
        if len(rows) < _BATCH:
            return list(reversed(collected))
        include_cursor = False
