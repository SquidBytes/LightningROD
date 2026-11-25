"""FordConnect charge sessions: folded from the raw archive when the unplug sample arrives."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.fcon_raw_sample import FconRawSample
from web.queries.locations import LOCATION_MATCH_RADIUS_M, haversine_meters
from web.services.pipeline.derive import derive_session
from web.services.pipeline.observation import SessionObservation
from web.services.pipeline.sessions import upsert_session
from web.services.sources.fcon.history import telemetry_since_boundary
from web.services.sources.fcon.identity import SOURCE_SYSTEM
from web.services.sources.ford_metrics.samples import charge_sample


def schedule_location_name(schedules: dict | None, lat: float, lon: float) -> str | None:
    """The user's own name for a saved charge-schedule location at these coordinates."""
    for entry in (schedules or {}).get("chargeSchedules") or []:
        loc = entry.get("location") or {}
        s_lat, s_lon, name = loc.get("latitude"), loc.get("longitude"), loc.get("name")
        if not isinstance(s_lat, (int, float)) or not isinstance(s_lon, (int, float)):
            continue
        if name and haversine_meters(lat, lon, float(s_lat), float(s_lon)) <= LOCATION_MATCH_RADIUS_M:
            return str(name)
    return None


def _is_unplug(payload: dict) -> bool:
    sample = charge_sample(payload)
    return sample is not None and sample.unplugged


async def _latest_schedules(db: AsyncSession, config_id: int | None) -> dict | None:
    stmt = select(FconRawSample.payload).where(FconRawSample.endpoint == "charge-schedules")
    if config_id is not None:
        stmt = stmt.where(FconRawSample.config_id == config_id)
    return await db.scalar(stmt.order_by(FconRawSample.last_seen_at.desc()).limit(1))


async def record_session_if_ended(
    db: AsyncSession, device_id: str, payload: dict, config_id: int | None = None
) -> EVChargingSession | None:
    """If this telemetry sample is an unplug that closes a charge, write the session."""
    current = charge_sample(payload)
    if current is None or not current.unplugged:
        return None
    payloads = await telemetry_since_boundary(db, device_id, current.at, _is_unplug)
    samples = [s for p in payloads if (s := charge_sample(p)) is not None]
    if not samples or samples[-1].at != current.at:
        samples.append(current)
    fields = derive_session(samples)
    if fields is None:
        return None
    name = None
    if fields["latitude"] is not None:
        name = schedule_location_name(
            await _latest_schedules(db, config_id), fields["latitude"], fields["longitude"]
        )
    return await upsert_session(
        db, SessionObservation(device_id, current.at, fields, SOURCE_SYSTEM, name)
    )
