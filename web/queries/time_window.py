"""Shared preset/custom date-window resolution for filter-bar queries."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_

_PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}


def _parse_iso_date(value: str | None) -> datetime | None:
    """Parse a yyyy-mm-dd string into a UTC start-of-day datetime, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return None


def resolve_time_window(
    time_range: str | None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Resolve a preset or custom date pair to a UTC (start, end) window.

    A non-'all' preset wins and yields (cutoff, None). Otherwise date_from /
    date_to (yyyy-mm-dd) map to inclusive start-of-day and end-of-day bounds;
    end is returned exclusive (date_to + 1 day). Either side may be None.
    """
    if time_range and time_range != "all":
        now = datetime.now(UTC)
        if time_range == "ytd":
            return datetime(now.year, 1, 1, tzinfo=UTC), None
        days = _PRESET_DAYS.get(time_range)
        return (now - timedelta(days=days), None) if days else (None, None)

    start = _parse_iso_date(date_from)
    end = _parse_iso_date(date_to)
    if end is not None:
        end += timedelta(days=1)
    return start, end


def window_clause(column, start: datetime | None, end: datetime | None):
    """Combine window bounds into a where clause on column; None when unbounded."""
    clauses = []
    if start is not None:
        clauses.append(column >= start)
    if end is not None:
        clauses.append(column < end)
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else and_(*clauses)
