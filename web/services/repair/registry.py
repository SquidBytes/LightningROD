"""Repair-operation registry: the catalog the Settings tab iterates."""

from __future__ import annotations

from web.services.repair.base import RepairOperation

# Populated as operations ship. Keep HA-dependent imports out of module
# import time — operations needing a runtime resolve it lazily.
REPAIR_REGISTRY: list[RepairOperation] = []


def get_operation(slug: str) -> RepairOperation | None:
    """Return the registered operation with this slug, or None."""
    for operation in REPAIR_REGISTRY:
        if operation.slug == slug:
            return operation
    return None
