"""One trip row per drive, whichever sources record it.

A second source merges into the first source's row through `PRECEDENCE` (see
`source_merge`); the same source reporting again only fills gaps and refreshes
regen and scores. Manual and imported trips are never changed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.services.pipeline.observation import INGEST_SCHEMA_VERSION, TripObservation
from web.services.source_merge import Rule, merge_values
from web.services.source_system import INGESTED, SourceSystem
from web.util.timestamps import parse_iso, utc

logger = logging.getLogger("lightningrod.pipeline.trips")

# A km-value stored alongside its double-converted twin sits near x1.609344.
RATIO_BAND = (1.55, 1.67)
END_TIME_WINDOW_SECONDS = 120.0
# The two trip sources disagree by seconds on where a drive begins, never by
# minutes: a far-off start means a different drive that merely ended alongside.
START_TIME_WINDOW_SECONDS = 120.0
# Only distance carries the unit bug, so a twin's duration and kWh must still
# agree. The band is wide enough for source rounding drift, far tighter than
# the x1.609 spread two genuinely different drives show.
SAME_DRIVE_TOLERANCE = 0.25

TEMP_FIELDS: tuple[str, ...] = ("ambient_temp", "cabin_temp", "outside_air_temp")
# Regen and coaching values: Ford keeps revising these after the drive ends.
SCORE_FIELDS: tuple[str, ...] = (
    "charge_regenerated",
    "speed_score",
    "acceleration_score",
    "deceleration_score",
    "electrical_efficiency",
    "range_regenerated",
    "brake_torque",
)

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
    fcon_side, ha_side = (incoming, current) if incoming_source == SourceSystem.FCON else (current, incoming)
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
    "trip_id": Rule(SourceSystem.HA_FORDPASS, "HA's id is derived from the vehicle's own trip timestamp"),
    "start_time": Rule(SourceSystem.FCON, "recomputed as end_time minus duration once both are merged"),
    "end_time": Rule(SourceSystem.FCON, "ignition-off from the TCU; HA's elveh path stamps receipt time"),
    "duration": Rule(SourceSystem.HA_FORDPASS, "the vehicle's own count; FCON's starts at its first ON poll"),
    "distance": Rule(
        SourceSystem.HA_FORDPASS, "HA's decimals if the FCON odometer agrees, else FCON", resolve=checked_distance
    ),
    "odometer_start": Rule(SourceSystem.FCON, "read at the boundary; HA picks the nearest status row"),
    "odometer_end": Rule(SourceSystem.FCON, "read at the boundary; HA picks the nearest status row"),
    "energy_consumed": Rule(SourceSystem.HA_FORDPASS, "measured by the vehicle; FCON's is a pack-estimate delta"),
    "range_regenerated": Rule(SourceSystem.HA_FORDPASS, "elveh is canonical for regen"),
    "ambient_temp": Rule(SourceSystem.HA_FORDPASS, "events trip summary is canonical for temperatures"),
    "cabin_temp": Rule(SourceSystem.HA_FORDPASS, "events trip summary is canonical for temperatures"),
    "outside_air_temp": Rule(SourceSystem.HA_FORDPASS, "events trip summary is canonical for temperatures"),
    "charge_regenerated": Rule(SourceSystem.HA_FORDPASS, "elveh is canonical for regen"),
    "speed_score": Rule(SourceSystem.HA_FORDPASS, "elveh is canonical for scores"),
    "acceleration_score": Rule(SourceSystem.HA_FORDPASS, "elveh is canonical for scores"),
    "deceleration_score": Rule(SourceSystem.HA_FORDPASS, "elveh is canonical for scores"),
    "electrical_efficiency": Rule(SourceSystem.HA_FORDPASS, "elveh is canonical for scores"),
    "start_location_id": Rule(SourceSystem.FCON, "resolved from a measured GPS fix"),
    "end_location_id": Rule(SourceSystem.FCON, "resolved from a measured GPS fix"),
    "brake_torque": Rule(SourceSystem.FCON, "only FordConnect reports it"),
    # Deterministic so both arrival orders label the row the same way.
    "source_system": Rule(SourceSystem.HA_FORDPASS, "one label whichever source arrived first"),
}

# Recomputed from the merged inputs, never taken from either side.
DERIVED = ("efficiency",)
_MATCH_FIELDS = (
    "device_id", "start_time", "end_time", "duration", "distance", "energy_consumed",
)


def _measurements_agree(val_a: Any, val_b: Any) -> bool:
    """One drive measured twice: values agree, or a source never recorded it."""
    if val_a is None or val_b is None:
        return True
    num_a, num_b = float(val_a), float(val_b)
    scale = max(abs(num_a), abs(num_b))
    return scale <= 0 or abs(num_a - num_b) / scale <= SAME_DRIVE_TOLERANCE


def _starts_agree(row_a: dict, row_b: dict) -> bool:
    """Same drive start, or a source never recorded one."""
    start_a = parse_iso(row_a.get("start_time"))
    start_b = parse_iso(row_b.get("start_time"))
    if start_a is None or start_b is None:
        return True
    return abs((start_b - start_a).total_seconds()) <= START_TIME_WINDOW_SECONDS


def is_unit_twin(row_a: dict, row_b: dict) -> bool:
    """Whether two rows can be one drive whose distance was written twice.

    The bug rewrote distance alone, so every other measurement the pair
    shares must still line up. Fields NULL on either side prove nothing and
    are left to the checks that can still run.
    """
    return (
        _starts_agree(row_a, row_b)
        and _measurements_agree(row_a.get("duration"), row_b.get("duration"))
        and _measurements_agree(
            row_a.get("energy_consumed"), row_b.get("energy_consumed")
        )
    )


def is_same_drive(row_a: dict, row_b: dict) -> bool:
    """Whether two rows record one drive, at equal distances or as x1.609 twins."""
    if row_a.get("device_id") != row_b.get("device_id"):
        return False
    end_a = parse_iso(row_a.get("end_time"))
    end_b = parse_iso(row_b.get("end_time"))
    if end_a is None or end_b is None:
        return False
    if abs((end_b - end_a).total_seconds()) > END_TIME_WINDOW_SECONDS:
        return False
    dist_a, dist_b = row_a.get("distance"), row_b.get("distance")
    if dist_a is not None and dist_b is not None:
        smaller, larger = sorted((float(dist_a), float(dist_b)))
        twin = smaller > 0 and RATIO_BAND[0] <= larger / smaller <= RATIO_BAND[1]
        if not (twin or _measurements_agree(dist_a, dist_b)):
            return False
    return is_unit_twin(row_a, row_b)


def is_mergeable(row: EVTripMetrics) -> bool:
    return row.source_system in INGESTED


def merge_changes(current: dict, incoming: dict, incoming_source: str) -> dict[str, Any]:
    """Columns to change on `current` after merging in `incoming`."""
    changes = merge_values(PRECEDENCE, current, incoming, incoming_source)
    merged = {**current, **changes}
    # FCON's first ON poll can trail the real start by minutes; the vehicle's
    # duration back from ignition-off does not.
    end, duration = utc(merged.get("end_time")), merged.get("duration")
    if end is not None and duration is not None:
        start = end - timedelta(seconds=float(duration))
        if utc(current.get("start_time")) != start:
            changes["start_time"] = start
        else:
            changes.pop("start_time", None)
    distance, energy = merged.get("distance"), merged.get("energy_consumed")
    if distance is not None and energy:
        efficiency = round(float(distance) / float(energy), 3)
        if current.get("efficiency") is None or float(current["efficiency"]) != efficiency:
            changes["efficiency"] = efficiency
    return changes


def row_values(row: EVTripMetrics) -> dict[str, Any]:
    return {f: utc(getattr(row, f)) for f in (*PRECEDENCE, *DERIVED, *_MATCH_FIELDS)}


def apply_merge(row: EVTripMetrics, incoming: dict, incoming_source: str) -> dict[str, Any]:
    changes = merge_changes(row_values(row), incoming, incoming_source)
    for field, value in changes.items():
        setattr(row, field, value)
    return changes


async def find_same_drive(db: AsyncSession, candidate: dict) -> EVTripMetrics | None:
    """A stored trip, from any source, that records the same drive as `candidate`."""
    end: datetime | None = utc(candidate.get("end_time"))
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
    candidate = {k: utc(v) for k, v in candidate.items()}
    return next((r for r in rows if is_same_drive(row_values(r), candidate)), None)


def _enrich_same_source(row: EVTripMetrics, values: dict[str, Any]) -> None:
    """A later report of a drive by its own source: never moves distance, energy or times."""
    for column, value in values.items():
        if value is not None and (column in SCORE_FIELDS or getattr(row, column) is None):
            setattr(row, column, value)


async def upsert_trip(db: AsyncSession, obs: TripObservation) -> EVTripMetrics | None:
    """Store one drive; returns its row, or None when a protected trip already records it."""
    source, values = obs.source_system, obs.values
    existing = await db.scalar(select(EVTripMetrics).where(EVTripMetrics.trip_id == obs.trip_id))
    if existing is not None and existing.source_system == source:
        # The same record again (a replay or re-parse) refreshes it.
        for column, value in values.items():
            setattr(existing, column, value)
        return existing
    existing = existing or await find_same_drive(db, {"device_id": obs.device_id, **values})
    if existing is not None:
        if not is_mergeable(existing):
            logger.info("%s trip matches protected trip %s; not written", source, existing.id)
            return None
        if existing.source_system == source:
            _enrich_same_source(existing, values)
        else:
            apply_merge(
                existing, {**values, "trip_id": obs.trip_id, "source_system": source}, source
            )
        return existing
    trip = EVTripMetrics(
        trip_id=obs.trip_id,
        device_id=obs.device_id,
        source_system=source,
        is_complete=True,
        recorded_at=values["end_time"],
        original_timestamp=values.get("start_time") or values["end_time"],
        ingest_schema_version=INGEST_SCHEMA_VERSION.get(source),
        **values,
    )
    db.add(trip)
    await db.flush()
    logger.info(
        "Stored %s trip: %s km, %s kWh", source, values.get("distance"), values.get("energy_consumed")
    )
    return trip
