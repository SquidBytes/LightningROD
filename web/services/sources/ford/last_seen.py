"""Newest raw value per mapped Ford metric, read back by /admin/data-sources."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from web.services.sources.ford.metrics import ParsedRow
from web.services.units.data_map import DataMapping


def record_last_seen(
    cache: dict[str, dict[str, Any]], data_map: list[DataMapping], rows: list[ParsedRow]
) -> None:
    """Note each parsed metric in `cache`, keyed by DataMapping.key."""
    by_field = {m.source_field: m for m in data_map}
    seen_at = datetime.now(UTC).isoformat()
    for row in rows:
        for metric, entry in row.raw.items():
            mapping = by_field[metric]
            cache[mapping.key] = {
                "value": entry.get("value") if isinstance(entry, dict) else entry,
                "unit": mapping.source_unit,
                "declared_unit": mapping.source_unit,
                "method": "declared",
                "seen_at": seen_at,
                "converted": row.values.get(mapping.db_column),
            }
