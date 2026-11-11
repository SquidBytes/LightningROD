"""Timestamp parsing and UTC coercion shared across ingestion, repair and queries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

logger = logging.getLogger("lightningrod.util.time")


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 string (`Z` or offset) or datetime to datetime; None if unparseable.

    Empty input is None silently; anything else that fails is logged, so a
    malformed source timestamp leaves a trace. The result is naive when the
    input carries no offset; pass it through `utc` or `to_utc`.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            logger.warning("Failed to parse datetime: %r", value)
            return None
    logger.warning("Failed to parse datetime: %r is not a string", value)
    return None


def utc[T](value: T) -> T:
    """Stamp UTC on a naive datetime; aware datetimes and non-datetimes pass through.

    SQLite hands timestamps back naive. Offsets are not re-based.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return cast(T, value.replace(tzinfo=UTC))
    return value


def to_utc[T](value: T) -> T:
    """UTC-aware copy: an aware datetime is re-based, a naive one stamped.

    Use before storing: SQLite drops tzinfo on bind, so a +02:00 timestamp
    would otherwise be stored as UTC wall time.
    """
    if isinstance(value, datetime) and value.tzinfo is not None:
        return cast(T, value.astimezone(UTC))
    return utc(value)
