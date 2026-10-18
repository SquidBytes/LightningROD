"""One trip row per drive when Home Assistant and FordConnect both record it.

Whichever source arrives second merges into the first source's row instead of
inserting, through `PRECEDENCE` (see `source_merge`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.services.source_merge import FCON, HA, MERGEABLE_SOURCES, Rule, merge_values

__all__ = ["FCON", "HA", "MERGEABLE_SOURCES", "PRECEDENCE", "apply_merge", "find_same_drive"]

logger = logging.getLogger("lightningrod.trip_merge")

# Each odometer end is a whole km (±0.5), so a delta is good to ±1 km; the
# captures show no key-on/off lag in the odometer, so nothing is added for timing.
DISTANCE_TOLERANCE_KM = 1.0


def _odometer_delta(row: dict) -> float | None:
    start, end = row.get("odometer_start"), row.get("odometer_end")
    if start is None or end is None:
        return None
    return float(end) - float(start)


def checked_distance(current: dict, incoming: dict, incoming_source: str) -> Any:
    """HA's precise distance when FordConnect's odometer confirms it, else FordConnect's.

    HA has converted distances twice before (the x1.609 twins); the odometer
    cannot be converted, so it is the referee. Only an FCON row reaches here
    with HA incoming, and HA rows with FCON incoming, so each side is known.
    """
    fcon_side, ha_side = (incoming, current) if incoming_source == FCON else (current, incoming)
    reference = _odometer_delta(fcon_side)
    if reference is None:
        reference = float(fcon_side["distance"])
    ha_distance = float(ha_side["distance"])
    if abs(ha_distance - reference) <= DISTANCE_TOLERANCE_KM:
        return ha_side["distance"]
    logger.warning(
        "Trip distance disagreement for %s ending %s: Home Assistant %.3f km, "
        "FordConnect odometer %.3f km; storing FordConnect's",
        current.get("device_id") or incoming.get("device_id"),
        current.get("end_time") or incoming.get("end_time"),
        ha_distance,
        reference,
    )
    return fcon_side["distance"]


PRECEDENCE: dict[str, Rule] = {
    "trip_id": Rule(HA, "HA's id is derived from the vehicle's own trip timestamp"),
    "start_time": Rule(FCON, "recomputed as end_time minus duration once both are merged"),
    "end_time": Rule(FCON, "ignition-off from the TCU; HA's elveh path stamps receipt time"),
    "duration": Rule(HA, "the vehicle's own count; FCON's starts at its first ON poll"),
    "distance": Rule(
        HA, "HA's decimals if the FCON odometer agrees, else FCON", resolve=checked_distance
    ),
    "odometer_start": Rule(FCON, "read at the boundary; HA picks the nearest status row"),
    "odometer_end": Rule(FCON, "read at the boundary; HA picks the nearest status row"),
    "energy_consumed": Rule(HA, "measured by the vehicle; FCON's is a pack-estimate delta"),
    "range_regenerated": Rule(HA, "elveh is canonical for regen"),
    "ambient_temp": Rule(HA, "events trip summary is canonical for temperatures"),
    "cabin_temp": Rule(HA, "events trip summary is canonical for temperatures"),
    "outside_air_temp": Rule(HA, "events trip summary is canonical for temperatures"),
    "driving_score": Rule(HA, "elveh is canonical for scores"),
    "speed_score": Rule(HA, "elveh is canonical for scores"),
    "acceleration_score": Rule(HA, "elveh is canonical for scores"),
    "deceleration_score": Rule(HA, "elveh is canonical for scores"),
    "electrical_efficiency": Rule(HA, "elveh is canonical for scores"),
    "start_location_id": Rule(FCON, "resolved from a measured GPS fix"),
    "end_location_id": Rule(FCON, "resolved from a measured GPS fix"),
    "brake_torque": Rule(FCON, "only FordConnect reports it"),
    # Deterministic so both arrival orders label the row the same way.
    "source_system": Rule(HA, "one label whichever source arrived first"),
}

# Recomputed from the merged inputs, never taken from either side.
DERIVED = ("efficiency",)
_MATCH_FIELDS = (
    "device_id", "start_time", "end_time", "duration", "distance", "energy_consumed",
)


def is_mergeable(row: EVTripMetrics) -> bool:
    return row.source_system in MERGEABLE_SOURCES


def merge_changes(current: dict, incoming: dict, incoming_source: str) -> dict[str, Any]:
    """Columns to change on `current` after merging in `incoming`."""
    changes = merge_values(PRECEDENCE, current, incoming, incoming_source)
    merged = {**current, **changes}
    # FCON's first ON poll can trail the real start by minutes; the vehicle's
    # duration back from ignition-off does not.
    end, duration = _aware(merged.get("end_time")), merged.get("duration")
    if end is not None and duration is not None:
        start = end - timedelta(seconds=float(duration))
        if _aware(current.get("start_time")) != start:
            changes["start_time"] = start
        else:
            changes.pop("start_time", None)
    distance, energy = merged.get("distance"), merged.get("energy_consumed")
    if distance is not None and energy:
        efficiency = round(float(distance) / float(energy), 3)
        if current.get("efficiency") is None or float(current["efficiency"]) != efficiency:
            changes["efficiency"] = efficiency
    return changes


def _aware(value: Any) -> Any:
    """SQLite hands timestamps back naive; they are UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def row_values(row: EVTripMetrics) -> dict[str, Any]:
    return {f: _aware(getattr(row, f)) for f in (*PRECEDENCE, *DERIVED, *_MATCH_FIELDS)}


def apply_merge(row: EVTripMetrics, incoming: dict, incoming_source: str) -> dict[str, Any]:
    changes = merge_changes(row_values(row), incoming, incoming_source)
    for field, value in changes.items():
        setattr(row, field, value)
    return changes


async def find_same_drive(db: AsyncSession, candidate: dict) -> EVTripMetrics | None:
    """A stored trip, from any source, that records the same drive as `candidate`."""
    from web.services.repair.ops.trip_duplicates import (
        END_TIME_WINDOW_SECONDS,
        is_same_drive,
    )

    end: datetime | None = _aware(candidate.get("end_time"))
    if end is None or candidate.get("device_id") is None:
        return None
    window = timedelta(seconds=END_TIME_WINDOW_SECONDS)
    rows = (
        await db.execute(
            select(EVTripMetrics)
            .where(
                EVTripMetrics.device_id == candidate["device_id"],
                EVTripMetrics.end_time >= end - window,
                EVTripMetrics.end_time <= end + window,
            )
            .order_by(EVTripMetrics.id)
        )
    ).scalars().all()
    candidate = {k: _aware(v) for k, v in candidate.items()}
    return next((r for r in rows if is_same_drive(row_values(r), candidate)), None)
