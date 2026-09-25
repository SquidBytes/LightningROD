"""GPS snapshots from FordConnect telemetry into ev_location."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.location import EVLocation
from web.services.sources.fcon.identity import SOURCE_SYSTEM
from web.services.sources.ford_metrics.parser import parse_time


async def record_position(db: AsyncSession, device_id: str, metrics: dict) -> EVLocation | None:
    """Store a measured fix once; dead-reckoned (INFERRED) positions are skipped."""
    entry = metrics.get("position")
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    at = parse_time(entry.get("updateTime"))
    if not isinstance(value, dict) or at is None or value.get("gpsCoordinateMethod") != "ACTUAL":
        return None
    loc = value.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    exists = await db.scalar(
        select(EVLocation.id).where(
            EVLocation.device_id == device_id, EVLocation.recorded_at == at
        )
    )
    if exists is not None:
        return None
    compass = metrics.get("compassDirection")
    row = EVLocation(
        device_id=device_id,
        recorded_at=at,
        latitude=float(lat),
        longitude=float(lon),
        altitude=loc.get("alt") if isinstance(loc.get("alt"), (int, float)) else None,
        compass_direction=compass.get("value") if isinstance(compass, dict) else None,
        source_system=SOURCE_SYSTEM,
        original_timestamp=at,
    )
    db.add(row)
    await db.flush()
    return row
