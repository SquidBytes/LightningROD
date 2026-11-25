"""Consolidate unit-corrupted duplicate trip pairs (the x1.609344 twins)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.services.pipeline.trips import (
    END_TIME_WINDOW_SECONDS,
    RATIO_BAND,
    SCORE_FIELDS,
    TEMP_FIELDS,
    is_unit_twin,
)
from web.services.repair.base import (
    DEFAULT_PREVIEW_LIMIT,
    RepairDiff,
    RepairGroup,
    RepairOperation,
    RepairPreview,
    mutable_only,
)
from web.util.timestamps import parse_iso


def _is_events_canonical(row: dict) -> bool:
    return any(row.get(f) is not None for f in TEMP_FIELDS)


def _is_elveh_canonical(row: dict) -> bool:
    return any(row.get(f) is not None for f in SCORE_FIELDS)


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
    + TEMP_FIELDS
    + SCORE_FIELDS
    + _MAX_FIELDS_SAFE
    + _NULL_FILL_FIELDS
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
        end_time = parse_iso(row.get("end_time"))
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

    for field in TEMP_FIELDS:
        val = _preferred(members, field, _is_events_canonical)
        if val is not None and val != survivor.get(field):
            merged[field] = val

    for field in SCORE_FIELDS:
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
    kept_end, removed_end = parse_iso(survivor["end_time"]), parse_iso(loser["end_time"])
    # Pairing already required both end times.
    gap = abs((removed_end - kept_end).total_seconds()) if kept_end and removed_end else 0.0
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


def pair_key(survivor: EVTripMetrics, loser: EVTripMetrics) -> str:
    """Stable group key: the pair's row ids, ascending."""
    low, high = sorted((survivor.id, loser.id))
    return f"pair:{low}-{high}"


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
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> list[tuple[EVTripMetrics, EVTripMetrics]]:
        # Pair over every candidate before filtering, so a skipped pair's rows
        # can never be re-paired with something else.
        rows = await self._load_candidates(db)
        by_pk = {row.id: row for row in rows}
        dict_pairs = find_unit_duplicate_pairs([_row_dict(r) for r in rows])
        pairs = [(by_pk[s["id"]], by_pk[lo["id"]]) for s, lo in dict_pairs]
        selection = await self.selection(db, keys)
        return selection.pick(pairs, lambda pair: pair_key(*pair))

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
                    key=pair_key(survivor, loser),
                )
            )
        return RepairPreview(groups, len(pairs), offset, limit, unit="pairs")

    async def affected_rows(
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> list[EVTripMetrics]:
        return [row for pair in await self._pairs(db, keys) for row in pair]

    async def execute(self, db: AsyncSession, keys: Iterable[str] | None = None) -> int:
        changed = 0
        for survivor, loser in await self._pairs(db, keys):
            merged = merge_pair(_row_dict(survivor), _row_dict(loser))
            for field, val in merged.items():
                setattr(survivor, field, val)
            await db.delete(loser)
            changed += 2
        await db.flush()
        return changed
