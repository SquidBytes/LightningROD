"""GPS fixes from a Ford `metrics` dict into ev_location."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.location import EVLocation
from web.queries.locations import haversine_meters
from web.util.timestamps import parse_iso, utc

# A fix this close in time and space to a stored one adds nothing to the track.
DEDUP_WINDOW = timedelta(seconds=60)
DEDUP_RADIUS_M = 50.0


async def record_position(
    db: AsyncSession, device_id: str, metrics: dict, source_system: str
) -> EVLocation | None:
    """Store a measured fix once; dead-reckoned (INFERRED) positions are skipped."""
    entry = metrics.get("position")
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    at = utc(parse_iso(entry.get("updateTime")))
    if not isinstance(value, dict) or at is None or value.get("gpsCoordinateMethod") != "ACTUAL":
        return None
    loc = value.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    nearby = (
        await db.execute(
            select(EVLocation.recorded_at, EVLocation.latitude, EVLocation.longitude).where(
                EVLocation.device_id == device_id,
                EVLocation.recorded_at >= at - DEDUP_WINDOW,
                EVLocation.recorded_at <= at + DEDUP_WINDOW,
            )
        )
    ).all()
    for stored_at, stored_lat, stored_lon in nearby:
        if utc(stored_at) == at:
            return None
        if stored_lat is None or stored_lon is None:
            continue
        if abs(utc(stored_at) - at) < DEDUP_WINDOW and (
            haversine_meters(float(stored_lat), float(stored_lon), float(lat), float(lon))
            < DEDUP_RADIUS_M
        ):
            return None
    compass = metrics.get("compassDirection")
    row = EVLocation(
        device_id=device_id,
        recorded_at=at,
        latitude=float(lat),
        longitude=float(lon),
        altitude=loc.get("alt") if isinstance(loc.get("alt"), (int, float)) else None,
        compass_direction=compass.get("value") if isinstance(compass, dict) else None,
        source_system=source_system,
        original_timestamp=at,
    )
    db.add(row)
    await db.flush()
    return row
