"""Consolidate unit-corrupted duplicate trip pairs (the x1.609344 twins)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.migrations.versions.p34_battery_trips_overhaul import (
    _SCORE_FIELDS,
    _TEMP_FIELDS,
    _coerce_datetime,
    _is_elveh_canonical,
    _is_events_canonical,
)
from db.models.trip_metrics import EVTripMetrics
from web.services.repair.base import (
    DEFAULT_PREVIEW_LIMIT,
    RepairDiff,
    RepairGroup,
    RepairOperation,
    RepairPreview,
    mutable_only,
)

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

# P34's _MAX_FIELDS also maxed distance/efficiency; here MAX would resurrect
# the corrupted larger distance, so only these two are safe to max-merge.
_MAX_FIELDS_SAFE: tuple[str, ...] = ("energy_consumed", "duration")

# Enrichment fields NULL-filled from the loser when the survivor lacks them.
_NULL_FILL_FIELDS: tuple[str, ...] = (
    "start_time",
    "odometer_start",
    "odometer_end",
    "start_location_id",
    "end_location_id",
)

_PAIR_FIELDS: tuple[str, ...] = (
    ("id", "device_id", "end_time", "distance", "efficiency")
    + _TEMP_FIELDS
    + _SCORE_FIELDS
    + _MAX_FIELDS_SAFE
    + _NULL_FILL_FIELDS
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
    start_a = _coerce_datetime(row_a.get("start_time"))
    start_b = _coerce_datetime(row_b.get("start_time"))
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


def find_unit_duplicate_pairs(
    rows: list[dict],
) -> list[tuple[dict, dict]]:
    """Greedily pair x1.609 duplicate twins; returns (survivor, loser) tuples.

    Survivor is the smaller-distance member (the larger one is the
    double-converted corruption). Each row joins at most one pair.
    """
    candidates: list[tuple[dict, Any, float]] = []
    for row in rows:
        if row.get("device_id") is None or row.get("distance") is None:
            continue
        end_time = _coerce_datetime(row.get("end_time"))
        if end_time is None:
            continue
        candidates.append((row, end_time, float(row["distance"])))

    pairs: list[tuple[dict, dict]] = []
    used: set[int] = set()
    for i, (row_a, ts_a, dist_a) in enumerate(candidates):
        if id(row_a) in used:
            continue
        for row_b, ts_b, dist_b in candidates[i + 1 :]:
            if id(row_b) in used:
                continue
            if row_b["device_id"] != row_a["device_id"]:
                continue
            if abs((ts_b - ts_a).total_seconds()) > END_TIME_WINDOW_SECONDS:
                continue
            smaller, larger = sorted((dist_a, dist_b))
            if smaller <= 0:
                continue
            ratio = larger / smaller
            if not (RATIO_BAND[0] <= ratio <= RATIO_BAND[1]):
                continue
            if not is_unit_twin(row_a, row_b):
                continue
            survivor, loser = (row_a, row_b) if dist_a <= dist_b else (row_b, row_a)
            pairs.append((survivor, loser))
            used.add(id(row_a))
            used.add(id(row_b))
            break
    return pairs


def _preferred(members: tuple[dict, dict], field: str, canonical) -> Any:
    """Field value from a canonical member, else any non-NULL member value."""
    for row in members:
        if canonical(row) and row.get(field) is not None:
            return row[field]
    for row in members:
        if row.get(field) is not None:
            return row[field]
    return None


def merge_pair(survivor: dict, loser: dict) -> dict:
    """Changed-fields dict for the survivor after merging in the loser.

    Distance and efficiency are never merged: the survivor's smaller
    distance is the correct one.
    """
    members = (survivor, loser)
    merged: dict[str, Any] = {}

    for field in _TEMP_FIELDS:
        val = _preferred(members, field, _is_events_canonical)
        if val is not None and val != survivor.get(field):
            merged[field] = val

    for field in _SCORE_FIELDS:
        val = _preferred(members, field, _is_elveh_canonical)
        if val is not None and val != survivor.get(field):
            merged[field] = val

    for field in _MAX_FIELDS_SAFE:
        values = [float(r[field]) for r in members if r.get(field) is not None]
        if values:
            val = max(values)
            current = survivor.get(field)
            if current is None or float(current) != val:
                merged[field] = val

    for field in _NULL_FILL_FIELDS:
        if survivor.get(field) is None and loser.get(field) is not None:
            merged[field] = loser[field]

    return merged


# What makes a trip recognisable at a glance, independent of what changes.
_IDENTITY_FIELDS: tuple[str, ...] = ("start_time", "end_time", "distance")


def _row_dict(row: EVTripMetrics) -> dict:
    return {field: getattr(row, field) for field in _PAIR_FIELDS}


def _identity(row: EVTripMetrics) -> dict:
    return {field: getattr(row, field) for field in _IDENTITY_FIELDS}


def pair_evidence(survivor: dict, loser: dict) -> dict[str, str]:
    """Why these two rows were paired: the x1.609 ratio and the end-time gap."""
    smaller, larger = float(survivor["distance"]), float(loser["distance"])
    gap = abs(
        (
            _coerce_datetime(loser["end_time"]) - _coerce_datetime(survivor["end_time"])
        ).total_seconds()
    )
    return {
        "distance ratio": f"{larger / smaller:.4g}x",
        "distances": f"{survivor['distance']} kept / {loser['distance']} removed",
        "end times": f"{gap:g}s apart",
        "match rule": (
            f"ratio within {RATIO_BAND[0]}-{RATIO_BAND[1]}, ends within "
            f"{END_TIME_WINDOW_SECONDS:g}s, and the same drive start, duration "
            f"and energy; the smaller distance is kept"
        ),
    }


class TripDuplicateConsolidation(RepairOperation):
    """Merge x1.609 duplicate trip pairs, keeping the smaller (correct) distance."""

    slug = "trip-duplicate-consolidation"
    display_name = "Trip duplicate consolidation"
    description = (
        "Finds duplicate trip pairs where one distance is the other x1.609 "
        "(a unit-conversion bug), merges their fields onto the correct row, "
        "and deletes the corrupted twin."
    )
    model = EVTripMetrics

    async def _load_candidates(self, db: AsyncSession) -> list[EVTripMetrics]:
        stmt = (
            select(EVTripMetrics)
            .where(
                mutable_only(EVTripMetrics),
                EVTripMetrics.end_time.is_not(None),
                EVTripMetrics.distance.is_not(None),
            )
            .order_by(EVTripMetrics.id)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def _pairs(
        self, db: AsyncSession
    ) -> list[tuple[EVTripMetrics, EVTripMetrics]]:
        rows = await self._load_candidates(db)
        by_pk = {row.id: row for row in rows}
        dict_pairs = find_unit_duplicate_pairs([_row_dict(r) for r in rows])
        return [(by_pk[s["id"]], by_pk[lo["id"]]) for s, lo in dict_pairs]

    async def census(self, db: AsyncSession) -> int:
        return len(await self._pairs(db))

    async def preview(
        self, db: AsyncSession, limit: int = DEFAULT_PREVIEW_LIMIT, offset: int = 0
    ) -> RepairPreview:
        pairs = await self._pairs(db)
        groups: list[RepairGroup] = []
        for survivor, loser in pairs[offset : offset + limit]:
            survivor_row, loser_row = _row_dict(survivor), _row_dict(loser)
            merged = merge_pair(survivor_row, loser_row)
            groups.append(
                RepairGroup(
                    [
                        RepairDiff(
                            survivor.id,
                            # The whole row, not just what changes: the group
                            # renders a cell per member per field, so a member
                            # that omits a field reads as holding nothing.
                            survivor_row,
                            merged,
                            "update",
                            identity=_identity(survivor),
                            role="keep",
                        ),
                        RepairDiff(
                            loser.id,
                            loser_row,
                            None,
                            "delete",
                            identity=_identity(loser),
                            role="duplicate",
                        ),
                    ],
                    label=f"Trips #{survivor.id} + #{loser.id}",
                    context=pair_evidence(survivor_row, loser_row),
                )
            )
        return RepairPreview(groups, len(pairs), offset, limit, unit="pairs")

    async def affected_rows(self, db: AsyncSession) -> list[EVTripMetrics]:
        return [row for pair in await self._pairs(db) for row in pair]

    async def execute(self, db: AsyncSession) -> int:
        changed = 0
        for survivor, loser in await self._pairs(db):
            merged = merge_pair(_row_dict(survivor), _row_dict(loser))
            for field, val in merged.items():
                setattr(survivor, field, val)
            await db.delete(loser)
            changed += 2
        await db.flush()
        return changed
