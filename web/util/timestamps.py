"""Timestamp parsing and UTC coercion shared across ingestion, repair, queries and forms."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo
from typing import Any, cast


def parse_iso(value: Any, *, logger: logging.Logger | None = None) -> datetime | None:
    """ISO-8601 string (`Z` or offset) or datetime to datetime; None if unparseable.

    Silent by default: user input (CSV rows, filters) reports its own errors.
    Pass `logger` for machine-sourced timestamps so a malformed one leaves a
    trace. The result is naive when the input carries no offset; pass it
    through `utc` or `from_local`.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    if logger is not None:
        logger.warning("Failed to parse datetime: %r", value)
    return None


def utc[T](value: T) -> T:
    """UTC-aware copy of a datetime; non-datetimes pass through.

    A naive value is stamped UTC (SQLite hands timestamps back naive); an aware
    one is re-based, because SQLite drops tzinfo on bind and would otherwise
    store a +02:00 wall time as UTC.
    """
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return cast(T, value.replace(tzinfo=UTC))
    return cast(T, value.astimezone(UTC))


def from_local(value: datetime, tz: tzinfo) -> datetime:
    """UTC instant of a wall-clock time read in `tz`; an aware input is just re-based."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(UTC)
