"""FordConnect trips: folded from the raw archive when the ignition-off sample arrives."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.queries.trips import detect_trip_location_ids
from web.services.pipeline.derive import derive_trip
from web.services.pipeline.observation import TripObservation
from web.services.pipeline.trips import upsert_trip
from web.services.sources.fcon.history import telemetry_since_boundary
from web.services.sources.fcon.identity import SOURCE_SYSTEM
from web.services.sources.ford_metrics.parser import parse_time
from web.services.sources.ford_metrics.samples import trip_sample


def trip_uuid(device_id: str, start: datetime) -> uuid.UUID:
    """Stable id: the same drive re-derived keeps its row."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"lightningrod:fcon-trip:{device_id}|{start.isoformat()}")


def _is_ignition_off(payload: dict) -> bool:
    sample = trip_sample(payload)
    return sample is not None and not sample.ignition_on


async def record_trip_if_ended(
    db: AsyncSession, device_id: str, payload: dict
) -> EVTripMetrics | None:
    """If this telemetry sample switches the vehicle off after a drive, write the trip."""
    current = trip_sample(payload)
    if current is None or current.ignition_on:
        return None
    until = parse_time(payload.get("updateTime")) or current.at
    payloads = await telemetry_since_boundary(
        db, device_id, until, _is_ignition_off, include_boundary=True
    )
    samples = [s for p in payloads if (s := trip_sample(p)) is not None]
    if not samples or samples[-1] != current:
        samples.append(current)
    fields = derive_trip(samples)
    if fields is None:
        return None
    start_loc, end_loc = await detect_trip_location_ids(
        db, device_id, fields["start_time"], fields["end_time"]
    )
    fields.update(start_location_id=start_loc, end_location_id=end_loc)
    trip_id = trip_uuid(device_id, fields["start_time"])
    return await upsert_trip(db, TripObservation(device_id, trip_id, fields, SOURCE_SYSTEM))
