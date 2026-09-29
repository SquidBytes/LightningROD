"""One trip row per drive when Home Assistant and FordConnect both record it.

Whichever source arrives second merges into the first source's row instead of
inserting. A field NULL on one side is filled from the other; where both hold
a value, `PRECEDENCE` names the winner, the same table in both directions, so
the final row does not depend on arrival order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics

HA = "ha_fordpass"
FCON = "fcon"
# Ingested rows a same-drive merge may enrich; manual and imported rows never.
MERGEABLE_SOURCES = (HA, FCON)


@dataclass(frozen=True)
class Rule:
    winner: str
    why: str


PRECEDENCE: dict[str, Rule] = {
    "trip_id": Rule(HA, "HA's id is derived from the vehicle's own trip timestamp"),
    "start_time": Rule(FCON, "ignition-on time from the TCU; HA's is end minus duration"),
    "end_time": Rule(FCON, "ignition-off time from the TCU; HA's is event receipt time"),
    "duration": Rule(FCON, "kept consistent with the start/end it is derived from"),
    "distance": Rule(HA, "trip meter has decimals; the FCON odometer is whole km"),
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
    "source_system": Rule(HA, "a merged row is labelled by the distance winner"),
}

# Recomputed from the merged inputs, never taken from either side.
DERIVED = ("efficiency",)
_MATCH_FIELDS = (
    "device_id", "start_time", "end_time", "duration", "distance", "energy_consumed",
)


def is_mergeable(row: EVTripMetrics) -> bool:
    return row.source_system in MERGEABLE_SOURCES


def _is_twin_pair(a: Any, b: Any) -> bool:
    from web.services.repair.ops.trip_duplicates import RATIO_BAND

    if a is None or b is None:
        return False
    small, large = sorted((float(a), float(b)))
    return small > 0 and RATIO_BAND[0] <= large / small <= RATIO_BAND[1]


def merge_changes(current: dict, incoming: dict, incoming_source: str) -> dict[str, Any]:
    """Columns to change on `current` after merging in `incoming`."""
    changes: dict[str, Any] = {}
    for field, rule in PRECEDENCE.items():
        new = incoming.get(field)
        if new is None:
            continue
        old = current.get(field)
        if old is None or (rule.winner == incoming_source and old != new):
            changes[field] = new
    # Of an x1.609 twin pair the smaller distance is the correct one.
    old_d, new_d = current.get("distance"), incoming.get("distance")
    if old_d is not None and new_d is not None and _is_twin_pair(old_d, new_d):
        if float(old_d) <= float(new_d):
            changes.pop("distance", None)
        else:
            changes["distance"] = new_d
    merged = {**current, **changes}
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
