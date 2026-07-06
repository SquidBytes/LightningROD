"""Repair-operation registry: the catalog the Settings tab iterates."""

from __future__ import annotations

from web.services.repair.base import RepairOperation
from web.services.repair.ops.trip_distance_double_conversion import (
    TripDistanceDoubleConversion,
)
from web.services.repair.ops.trip_duplicates import TripDuplicateConsolidation

# Registry order is execution/render order: consolidation must run before
# double-conversion so surviving rows are the ones ratio-checked. Keep
# HA-dependent imports out of module import time — operations needing a
# runtime resolve it lazily.
REPAIR_REGISTRY: list[RepairOperation] = [
    TripDuplicateConsolidation(),
    TripDistanceDoubleConversion(),
]


def get_operation(slug: str) -> RepairOperation | None:
    """Return the registered operation with this slug, or None."""
    for operation in REPAIR_REGISTRY:
        if operation.slug == slug:
            return operation
    return None
