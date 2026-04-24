"""Unit-contract datatypes shared by source adapters and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FieldContract:
    """Mapping from one source attribute to one target DB column in metric units.

    `ha_unit_system_converted` flags fields that Home Assistant itself converts
    before emitting — i.e. fields where ha-fordpass calls `localize_distance` /
    `localize_temperature` / `units.pressure()` inside its `attrs_fn`. For
    those fields the effective source unit at read-time depends on
    `ha_config.unit_system` (imperial -> mi/°F, metric -> km/°C), and the
    adapter's `_resolve_source_unit` derives it per-event. Leave False for
    raw-API passthrough fields (metrics/events entities, raw attributes on
    energytransferlogentry, etc.).
    """

    source_entity_pattern: str
    source_attribute: str
    source_unit: str
    target_db_table: str
    target_db_column: str
    target_unit: str
    notes: Optional[str] = None
    ha_unit_system_converted: bool = False
