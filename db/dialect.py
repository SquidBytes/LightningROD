"""Dialect-portable SQL expression helpers."""
from sqlalchemy import DateTime, Integer, cast, func, type_coerce
from sqlalchemy.engine import Dialect

_STRFTIME_FORMATS = {
    "hour":  "%Y-%m-%d %H:00:00",
    "day":   "%Y-%m-%d 00:00:00",
    "month": "%Y-%m-01 00:00:00",
    "year":  "%Y-01-01 00:00:00",
}


def date_trunc_compat(unit: str, col, *, dialect: Dialect):
    """Dialect-portable date_trunc. Returns DateTime expression.

    PG: emits date_trunc(unit, col).
    SQLite: emits strftime(fmt, col) for fixed units, or datetime(unixepoch)
            for multi-hour units ('2 hours', '4 hours', '6 hours'). The result
            is wrapped in type_coerce(..., DateTime) so SQLAlchemy parses the
            string back to a Python datetime — we deliberately avoid SQL
            ``CAST(... AS DATETIME)`` because SQLite's NUMERIC affinity for
            DATETIME extracts the year as an integer (e.g. '2026-01-01' -> 2026).

    Caller passes dialect=db.bind.dialect.
    """
    if dialect.name == "postgresql":
        return func.date_trunc(unit, col)

    # SQLite path
    if unit in _STRFTIME_FORMATS:
        return type_coerce(func.strftime(_STRFTIME_FORMATS[unit], col), DateTime)

    if unit.endswith(" hours") or unit.endswith(" hour"):
        n = int(unit.split()[0])
        # Truncate to N-hour boundary via integer arithmetic on unix epoch seconds.
        # type_coerce(..., Integer) on the strftime('%s', ...) output declares the
        # python-side type. SQLAlchemy still emits float division on the SQL side
        # (the ``+ 0.0`` coercion is automatic), so we wrap the division in
        # ``cast(..., Integer)`` to force SQLite to floor before the multiply —
        # without this, every row lands in its own bucket on the SQL side.
        epoch = type_coerce(func.strftime("%s", col), Integer)
        bucket_seconds = cast(epoch / (n * 3600), Integer) * (n * 3600)
        return type_coerce(func.datetime(bucket_seconds, "unixepoch"), DateTime)

    raise ValueError(f"Unsupported date_trunc unit on SQLite: {unit!r}")
