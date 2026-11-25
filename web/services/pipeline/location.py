"""Measured GPS fixes into ev_location."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.location import EVLocation
from web.queries.locations import haversine_meters
from web.services.pipeline.observation import PositionObservation
from web.util.timestamps import utc

# A fix this close in time and space to a stored one adds nothing to the track.
DEDUP_WINDOW = timedelta(seconds=60)
DEDUP_RADIUS_M = 50.0


async def record_position(db: AsyncSession, obs: PositionObservation | None) -> EVLocation | None:
    """Store a fix once, whichever source reports it."""
    if obs is None:
        return None
    at = utc(obs.recorded_at)
    nearby = (
        await db.execute(
            select(EVLocation.recorded_at, EVLocation.latitude, EVLocation.longitude).where(
                EVLocation.device_id == obs.device_id,
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
            haversine_meters(float(stored_lat), float(stored_lon), obs.latitude, obs.longitude)
            < DEDUP_RADIUS_M
        ):
            return None
    row = EVLocation(
        device_id=obs.device_id,
        recorded_at=at,
        latitude=obs.latitude,
        longitude=obs.longitude,
        altitude=obs.altitude,
        compass_direction=obs.compass_direction,
        source_system=obs.source_system,
        original_timestamp=at,
    )
    db.add(row)
    await db.flush()
    return row
