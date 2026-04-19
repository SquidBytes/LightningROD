"""Unit-contract datatypes shared by source adapters and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FieldContract:
    """Mapping from one source attribute to one target DB column in metric units."""

    source_entity_pattern: str
    source_attribute: str
    source_unit: str
    target_db_table: str
    target_db_column: str
    target_unit: str
    notes: Optional[str] = None
