"""Dialect-portable SQL expression helpers."""
from sqlalchemy import DateTime, cast, func
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
    SQLite: emits CAST(strftime(fmt, col) AS DATETIME) for fixed units, or
            integer-arithmetic bucketing on unix epoch for multi-hour units
            ('2 hours', '4 hours', '6 hours').

    Caller passes dialect=db.bind.dialect.
    """
    if dialect.name == "postgresql":
        return func.date_trunc(unit, col)

    # SQLite path
    if unit in _STRFTIME_FORMATS:
        return cast(func.strftime(_STRFTIME_FORMATS[unit], col), DateTime)

    if unit.endswith(" hours") or unit.endswith(" hour"):
        n = int(unit.split()[0])
        # Truncate to N-hour boundary via integer arithmetic on unix epoch seconds.
        return cast(
            func.datetime(
                (cast(func.strftime("%s", col), DateTime) / (n * 3600)) * (n * 3600),
                "unixepoch",
            ),
            DateTime,
        )

    raise ValueError(f"Unsupported date_trunc unit on SQLite: {unit!r}")
