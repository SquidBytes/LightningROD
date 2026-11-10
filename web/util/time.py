"""Timestamp parsing and UTC coercion shared across ingestion, repair and queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 string (`Z` or offset) or datetime to datetime; None if unparseable.

    The result is naive when the input carries no offset; pass it through `utc`.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def utc[T](value: T) -> T:
    """Stamp UTC on a naive datetime; aware datetimes and non-datetimes pass through.

    SQLite hands timestamps back naive. Offsets are not re-based.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return cast(T, value.replace(tzinfo=UTC))
    return value
