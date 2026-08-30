"""Shared seeding facilities: time-shift helper, mapping-driven generator, gap reporter."""
from __future__ import annotations

import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Import the model classes we'll query for MAX timestamps.
from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.location import EVLocation
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle_status import EVVehicleStatus
from web.services.units.data_map import DataMapping

# (table, timestamp_column_attr) tuples that define the universe of "demo data time"
# Note: EVTripMetrics uses `end_time` (not `trip_end_utc` — that column does not exist)
TIMESTAMP_SOURCES: list[tuple[type, str]] = [
    (EVChargingSession, "session_end_utc"),
    (EVBatteryStatus, "recorded_at"),
    (EVTripMetrics, "end_time"),
    (EVVehicleStatus, "recorded_at"),
    (EVLocation, "recorded_at"),
]


@dataclass(frozen=True)
class OffsetResult:
    offset: timedelta
    max_observed: datetime | None
    now: datetime

    @property
    def has_data(self) -> bool:
        return self.max_observed is not None


async def compute_global_offset(
    db: AsyncSession,
    *,
    sources: Iterable[tuple[Any, str]] | None = None,
    now: datetime | None = None,
) -> OffsetResult:
    """Compute a single global time offset = now - max(timestamp across all sources).

    Used to shift ALL demo timestamps uniformly so the most recent row is "now"
    while preserving relative spacing between rows. If no rows exist, offset is
    timedelta(0) and max_observed is None.
    """
    sources = TIMESTAMP_SOURCES if sources is None else sources
    now = now or datetime.now(UTC)

    max_observed: datetime | None = None
    for model, ts_attr in sources:
        col = getattr(model, ts_attr)
        result = await db.execute(select(func.max(col)))
        row_max = result.scalar()
        if row_max is None:
            continue
        # Normalize to UTC-aware
        if row_max.tzinfo is None:
            row_max = row_max.replace(tzinfo=UTC)
        if max_observed is None or row_max > max_observed:
            max_observed = row_max

    if max_observed is None:
        return OffsetResult(offset=timedelta(0), max_observed=None, now=now)
    return OffsetResult(offset=now - max_observed, max_observed=max_observed, now=now)


def shift_datetime(value: datetime | None, offset: timedelta) -> datetime | None:
    """Apply offset to a single datetime; returns None unchanged."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value + offset


async def apply_offset_to_table(
    db: AsyncSession,
    model: type,
    ts_columns: Iterable[str],
    offset: timedelta,
) -> int:
    """Bulk-shift timestamp columns on every row of a table. Returns rows updated.

    Dialect-aware:
      * PostgreSQL — `column + offset` (native interval arithmetic).
      * SQLite     — `strftime('%Y-%m-%d %H:%M:%f', column, '±N seconds')`.
        SQLite has no interval type; SQLAlchemy's `col + timedelta` on this
        dialect compiles to a numeric coercion that destroys the column.
    Idempotent shift = noop only if offset is zero.
    """
    if offset == timedelta(0):
        return 0
    from sqlalchemy import func, update

    dialect_name = db.bind.dialect.name
    seconds = offset.total_seconds()
    sqlite_modifier = f"{'+' if seconds >= 0 else '-'}{abs(seconds)} seconds"

    rows_updated = 0
    for ts_col_name in ts_columns:
        col = getattr(model, ts_col_name)
        if dialect_name == "sqlite":
            shifted = func.strftime("%Y-%m-%d %H:%M:%f", col, sqlite_modifier)
        else:
            shifted = col + offset
        stmt = update(model).values({ts_col_name: shifted}).where(col.isnot(None))
        result = await db.execute(stmt)
        rowcount = getattr(result, "rowcount", 0) or 0
        rows_updated = max(rows_updated, rowcount)
    return rows_updated


# ---------------------------------------------------------------------------
# Mapping-driven value generation
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# Canonical unit aliases — keys are normalised (lower, no spaces, no "/").
# Values map to a handler tag used by realistic_value().
_UNIT_ALIASES: dict[str, str] = {
    "kwh": "kWh",
    "%": "%",
    "percent": "%",
    "°c": "°C",
    "degc": "°C",
    "c": "°C",
    "km": "km",
    "mi": "mi",
    "kmh": "km/h",
    "kmph": "km/h",
    "km/h": "km/h",
    "v": "V",
    "kpa": "kPa",
    "psi": "psi",
    "a": "A",
    "kw": "kW",
    "s": "s",
    "sec": "s",
    "seconds": "s",
    "min": "min",
    "minutes": "min",
    "bool": "bool",
    "str": "str",
    "string": "str",
}

_DEFAULT_RNG = random.Random(42)


def _normalise_unit(raw: str) -> str:
    """Lower-case, strip spaces and forward-slashes for alias lookup."""
    return raw.lower().replace("/", "").replace(" ", "")


def realistic_value(
    db_unit: str,
    context: dict | None = None,
    rng: random.Random | None = None,
) -> object:
    """Return a plausible seed value for *db_unit*.

    Defaults to a seeded ``random.Random(42)`` so seed runs are reproducible.
    """
    r = rng or _DEFAULT_RNG
    tag = _UNIT_ALIASES.get(_normalise_unit(db_unit))

    if tag == "kWh":
        return round(r.uniform(5, 80), 2)
    if tag == "%":
        return round(r.uniform(5, 100), 1)
    if tag == "°C":
        raw = r.gauss(15, 12)
        return round(max(-10.0, min(40.0, raw)), 1)
    if tag == "km":
        return round(r.uniform(0, 500), 1)
    if tag == "mi":
        return round(r.uniform(0, 310), 1)
    if tag == "km/h":
        return round(r.uniform(0, 120), 1)
    if tag == "V":
        return round(r.uniform(200, 500), 1)
    if tag == "kPa":
        return round(r.uniform(200, 280), 1)
    if tag == "psi":
        return round(r.uniform(28, 40), 1)
    if tag == "A":
        return round(r.uniform(0, 200), 1)
    if tag == "kW":
        return round(r.uniform(1, 250), 1)
    if tag == "s":
        return int(r.uniform(60, 7200))
    if tag == "min":
        return int(r.uniform(1, 120))
    if tag == "bool":
        return r.choice([True, False])
    if tag == "str":
        return (context or {}).get("value", "unknown")

    _log.warning("realistic_value: unknown db_unit %r — returning None", db_unit)
    return None


class DataMapSeeder:
    """Generate realistic seed values driven by ``DATA_MAP`` declarations.

    `expected` holds mappings a seed module needs that the adapter has not
    declared yet; using one records a gap for the post-run report.
    """

    def __init__(
        self,
        declared: list[DataMapping],
        expected: list[DataMapping] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._declared: dict[tuple[str, str], DataMapping] = {
            (fc.db_table, fc.db_column): fc for fc in declared
        }
        self._expected: dict[tuple[str, str], DataMapping] = {
            (fc.db_table, fc.db_column): fc for fc in (expected or [])
        }
        self._gaps: dict[tuple[str, str], DataMapping] = {}
        self._rng = rng or random.Random(42)

    # ------------------------------------------------------------------
    def value_for(
        self, table: str, column: str, context: dict | None = None
    ) -> object:
        """Return a realistic value for the given *(table, column)* pair.

        Raises ``KeyError`` when neither declared nor expected knows the pair.
        """
        key = (table, column)

        fc = self._declared.get(key)
        if fc is not None:
            return realistic_value(fc.db_unit, context=context, rng=self._rng)

        fc = self._expected.get(key)
        if fc is not None:
            self._gaps[key] = fc
            return realistic_value(fc.db_unit, context=context, rng=self._rng)

        raise KeyError(f"No mapping for {table}.{column}")

    def gaps_report(self) -> list[DataMapping]:
        """Return deduped gap mappings sorted by (table, column)."""
        return sorted(self._gaps.values(), key=lambda fc: (fc.db_table, fc.db_column))


# ---------------------------------------------------------------------------
# Gap report writer
# ---------------------------------------------------------------------------


def write_data_map_gap_report(gaps: list[DataMapping], path: Path) -> None:
    """Write a Markdown gap report to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not gaps:
        path.write_text(
            "# Data Map Gap Report\n\n"
            f"Generated {now_iso}.\n\n"
            "No gaps detected — all seeded fields have declared mappings.\n",
            encoding="utf-8",
        )
        return

    lines: list[str] = [
        "# Data Map Gap Report",
        "",
        f"Generated by the demo seed run on {now_iso}. The seed populated values for the",
        "following `(table, column)` pairs that are NOT yet declared in `DATA_MAP` inside",
        "`web/services/sources/ha_fordpass/adapter.py`. Add the blocks below to that file",
        "to close the gap, then re-run `uv run python -m scripts.seed.main --all` and confirm this",
        "report comes back empty.",
        "",
        f"Total gaps: {len(gaps)}",
    ]

    # Group by table
    from itertools import groupby

    for table, group in groupby(gaps, key=lambda fc: fc.db_table):
        lines.append("")
        lines.append(f"## {table}")
        lines.append("")
        for fc in group:
            notes_val = repr(fc.notes) if fc.notes else '"<fill in>"'
            lines.append("```python")
            lines.append("DataMapping(")
            lines.append('    source=SourceRef("<fill in>", SourceKind.HA_ENTITY),')
            lines.append('    source_field="<fill in>",')
            lines.append('    source_unit="<fill in>",')
            lines.append(f'    db_table="{fc.db_table}",')
            lines.append(f'    db_column="{fc.db_column}",')
            lines.append(f'    db_unit="{fc.db_unit}",')
            lines.append(f"    notes={notes_val},")
            lines.append("),")
            lines.append("```")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_declared_mappings() -> list[DataMapping]:
    """Import and return ``DATA_MAP`` from the HA Fordpass adapter."""
    from web.services.sources.ha_fordpass.adapter import DATA_MAP

    return list(DATA_MAP)
