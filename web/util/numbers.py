"""Number coercion shared across ingestion, units and importers."""

from __future__ import annotations

from typing import Any


def safe_float(value: Any) -> float | None:
    """`float(value)`, or None when it is None or not numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
