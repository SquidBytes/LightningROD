"""Replay archived Home Assistant events into a scratch database and diff it against live.

Usage:
    uv run python scripts/replay_diff.py --since 7d
    uv run python scripts/replay_diff.py --since 7d --device <device_id> --json
    uv run python scripts/replay_diff.py --since 2026-11-01 --scratch-url sqlite+aiosqlite:///tmp/replay.db

The live database (DATABASE_URL) is only read. The scratch database gets the
migrations, then every archived event in the window is replayed through the
ingestion entry point under the unit system recorded alongside it. The five
derived tables are then compared: row counts, rows present on one side only,
and column values on matched rows. Exit status is 0 when nothing differs,
1 when something does, 2 on a setup error.

The report shows device ids by their last four characters and never prints
coordinates or addresses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import event, func, inspect, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models.battery_status import EVBatteryStatus  # noqa: E402
from db.models.charging_session import EVChargingSession  # noqa: E402
from db.models.location import EVLocation  # noqa: E402
from db.models.raw_event import HARawEvent  # noqa: E402
from db.models.trip_metrics import EVTripMetrics  # noqa: E402
from db.models.vehicle_status import EVVehicleStatus  # noqa: E402
from web.services.repair.base import _aware  # noqa: E402
from web.services.repair.replay_core import (  # noqa: E402
    ReplayStats,
    replay_archive,
    short_device,
)

REPLAY_SOURCE_SYSTEM = "ha_fordpass"


@dataclass(frozen=True)
class TableSpec:
    """How one derived table is keyed, windowed and compared."""

    model: type
    key: tuple[str, ...]
    window: str
    # Columns the replay cannot reproduce: surrogate ids, arrival stamps, and
    # anything joined from the user's reference tables (which the scratch
    # database does not have). Listed in the report so nothing hides.
    ignored: frozenset[str] = frozenset({"id", "ingested_at"})


_SESSION_REFERENCE = frozenset(
    {
        "id",
        "ingested_at",
        "session_id",
        "network_id",
        "location_id",
        "stall_id",
        "location_name",
        "location_type",
        "address",
        "is_free",
        "cost",
        "cost_without_overrides",
        "cost_source",
        "estimated_cost",
        "charger_rated_kw",
        "needs_review",
        "review_type",
        "duplicate_of_id",
    }
)

TABLES: dict[str, TableSpec] = {
    "ev_battery_status": TableSpec(
        EVBatteryStatus, ("device_id", "recorded_at"), "recorded_at"
    ),
    "ev_vehicle_status": TableSpec(
        EVVehicleStatus, ("device_id", "recorded_at"), "recorded_at"
    ),
    "ev_location": TableSpec(
        EVLocation,
        ("device_id", "recorded_at"),
        "recorded_at",
        frozenset({"id", "ingested_at", "address", "location_type"}),
    ),
    "ev_trip_metrics": TableSpec(
        EVTripMetrics,
        ("trip_id",),
        "end_time",
        frozenset({"id", "ingested_at", "start_location_id", "end_location_id"}),
    ),
    "ev_charging_session": TableSpec(
        EVChargingSession,
        ("device_id", "session_start_utc"),
        "session_start_utc",
        _SESSION_REFERENCE,
    ),
}

# Values never printed, only whether they differ.
REDACTED_COLUMNS = frozenset({"latitude", "longitude", "address", "location_name"})


# ---------------------------------------------------------------------------
# Arguments and database setup
# ---------------------------------------------------------------------------


def parse_since(text: str, *, now: datetime) -> datetime:
    """`7d`, `36h` or an ISO date/datetime, as an aware UTC instant."""
    match = re.fullmatch(r"(\d+)([dh])", text.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return now - (
            timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
        )
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def default_scratch_url() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return (
        f"sqlite+aiosqlite:///{Path(tempfile.gettempdir()) / f'replay_diff_{stamp}.db'}"
    )


def same_database(url_a: str, url_b: str) -> bool:
    """True when two URLs plausibly name the same database."""
    a, b = make_url(url_a), make_url(url_b)
    if a.get_backend_name() != b.get_backend_name():
        return False
    if a.get_backend_name() == "sqlite":
        pa, pb = a.database or "", b.database or ""
        if not pa or not pb or pa == ":memory:" or pb == ":memory:":
            return pa == pb
        return Path(pa).resolve() == Path(pb).resolve()
    return (a.host, a.port, a.database) == (b.host, b.port, b.database)


def assert_scratch_is_not_live(scratch_url: str, live_url: str) -> None:
    if same_database(scratch_url, live_url):
        raise SystemExit(
            "refusing to run: --scratch-url names the live database (DATABASE_URL)"
        )


def make_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url)
    if engine.sync_engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys = ON")

    return engine


def migrate(url: str) -> None:
    """Apply the migrations to `url` in a subprocess; alembic's env runs its own loop."""
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"scratch migration failed:\n{result.stderr}")


async def assert_scratch_is_empty(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as db:
        for name, spec in TABLES.items():
            count = await db.scalar(select(func.count()).select_from(spec.model))
            if count:
                raise SystemExit(
                    f"refusing to run: scratch table {name} has {count} rows"
                )


async def prepare_scratch(url: str, live_url: str) -> AsyncEngine:
    """A migrated, empty scratch engine that is provably not the live database."""
    assert_scratch_is_not_live(url, live_url)
    migrate(url)
    engine = make_engine(url)
    await assert_scratch_is_empty(engine)
    return engine


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _norm(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return _aware(value).astimezone(UTC)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def values_equal(a: Any, b: Any, tolerance: float) -> bool:
    a, b = _norm(a), _norm(b)
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) <= tolerance
    return a == b


def _row_dict(row: Any) -> dict[str, Any]:
    return {
        attr.key: getattr(row, attr.key) for attr in inspect(type(row)).column_attrs
    }


def _key_of(row: dict[str, Any], spec: TableSpec) -> tuple[Any, ...]:
    return tuple(_norm(row[column]) for column in spec.key)


def _render(value: Any) -> str:
    value = _norm(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def render_key(key: tuple[Any, ...], spec: TableSpec) -> str:
    parts = []
    for column, value in zip(spec.key, key, strict=True):
        if column == "device_id":
            parts.append(short_device(value))
        elif column == "trip_id":
            parts.append(str(value)[:8])
        else:
            parts.append(_render(value))
    return " ".join(parts)


@dataclass
class ColumnDiff:
    key: str
    live: Any
    scratch: Any


@dataclass
class TableReport:
    table: str
    key: tuple[str, ...]
    window_column: str
    ignored_columns: list[str]
    live_count: int = 0
    scratch_count: int = 0
    matched: int = 0
    live_only: list[str] = field(default_factory=list)
    scratch_only: list[str] = field(default_factory=list)
    # column -> every differing matched row
    column_diffs: dict[str, list[ColumnDiff]] = field(default_factory=dict)
    # side -> (min, max) of the window column, to make recorded_at drift visible
    span: dict[str, list[str | None]] = field(default_factory=dict)

    @property
    def identical(self) -> bool:
        return (
            self.live_count == self.scratch_count
            and not self.live_only
            and not self.scratch_only
            and not self.column_diffs
        )


def _span(rows: Iterable[dict[str, Any]], column: str) -> list[str | None]:
    values = [_norm(r[column]) for r in rows if r.get(column) is not None]
    if not values:
        return [None, None]
    return [_render(min(values)), _render(max(values))]


def diff_rows(
    table: str,
    spec: TableSpec,
    live_rows: Sequence[dict[str, Any]],
    scratch_rows: Sequence[dict[str, Any]],
    *,
    tolerance: float,
) -> TableReport:
    report = TableReport(
        table,
        spec.key,
        spec.window,
        sorted(spec.ignored),
        live_count=len(live_rows),
        scratch_count=len(scratch_rows),
    )
    report.span = {
        "live": _span(live_rows, spec.window),
        "scratch": _span(scratch_rows, spec.window),
    }
    live_by_key = {_key_of(r, spec): r for r in live_rows}
    scratch_by_key = {_key_of(r, spec): r for r in scratch_rows}
    report.live_only = [
        render_key(k, spec) for k in live_by_key if k not in scratch_by_key
    ]
    report.scratch_only = [
        render_key(k, spec) for k in scratch_by_key if k not in live_by_key
    ]
    for key, live in live_by_key.items():
        scratch = scratch_by_key.get(key)
        if scratch is None:
            continue
        report.matched += 1
        for column, live_value in live.items():
            if column in spec.ignored or column in spec.key:
                continue
            scratch_value = scratch.get(column)
            if values_equal(live_value, scratch_value, tolerance):
                continue
            if column in REDACTED_COLUMNS:
                shown_live = shown_scratch = "(redacted)"
            else:
                shown_live, shown_scratch = _render(live_value), _render(scratch_value)
            report.column_diffs.setdefault(column, []).append(
                ColumnDiff(render_key(key, spec), shown_live, shown_scratch)
            )
    return report


async def load_rows(
    db: AsyncSession,
    spec: TableSpec,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    devices: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Rows of one table; `since`/`until` bound the window column when given."""
    model = spec.model
    window = getattr(model, spec.window)
    stmt = select(model)
    if since is not None:
        stmt = stmt.where(window >= since)
    if until is not None:
        stmt = stmt.where(window <= until)
    if devices is not None:
        stmt = stmt.where(model.device_id.in_(tuple(devices)))
    if sources is not None:
        stmt = stmt.where(model.source_system.in_(tuple(sources)))
    rows = (await db.execute(stmt)).scalars().all()
    return [_row_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


@dataclass
class RunReport:
    since: str
    until: str
    devices: list[str]
    scratch_url: str
    tolerance: float
    replay: ReplayStats
    tables: list[TableReport]

    @property
    def identical(self) -> bool:
        return all(t.identical for t in self.tables)

    def to_json(self) -> str:
        data = asdict(self)
        data["identical"] = self.identical
        return json.dumps(data, indent=2, default=str)


def render_text(report: RunReport, *, examples: int) -> str:
    out: list[str] = []
    out.append(f"window      {report.since} .. {report.until}")
    out.append(f"devices     {', '.join(report.devices) or '(none in archive)'}")
    out.append(f"scratch     {report.scratch_url}")
    r = report.replay
    out.append(
        f"replayed    {r.replayed} events, {r.skipped_unknown_units} skipped "
        f"(no unit system), {r.errors} errors"
    )
    out.append(f"tolerance   {report.tolerance} on numeric columns")
    for f in r.failures:
        out.append(f"error       {f.slug} {f.exception} x{f.count}: {f.example}")
    for t in report.tables:
        out.append("")
        out.append(
            f"{t.table:<22} live {t.live_count:>6}  scratch {t.scratch_count:>6}  "
            f"matched {t.matched:>6}  live-only {len(t.live_only):>5}  "
            f"scratch-only {len(t.scratch_only):>5}"
        )
        out.append(f"  key {' + '.join(t.key)}; window on {t.window_column}")
        live_span, scratch_span = t.span.get("live"), t.span.get("scratch")
        if live_span and scratch_span and any(live_span + scratch_span):
            out.append(
                f"  {t.window_column}: live {live_span[0]} .. {live_span[1]}; "
                f"scratch {scratch_span[0]} .. {scratch_span[1]}"
            )
            if scratch_span[1] and scratch_span[1] > report.until:
                out.append(
                    f"  note: scratch {t.window_column} lies after the window; "
                    "these rows were stamped at replay time, not reading time"
                )
        if t.ignored_columns:
            out.append(f"  ignored columns: {', '.join(t.ignored_columns)}")
        for label, keys in (
            ("live-only", t.live_only),
            ("scratch-only", t.scratch_only),
        ):
            if keys:
                shown = "; ".join(keys[:examples])
                more = f"; +{len(keys) - examples} more" if len(keys) > examples else ""
                out.append(f"  {label}: {shown}{more}")
        for column, diffs in sorted(t.column_diffs.items()):
            out.append(f"  {column}: {len(diffs)} rows differ")
            for d in diffs[:examples]:
                out.append(f"    {d.key}  live {d.live}  scratch {d.scratch}")
            if len(diffs) > examples:
                out.append(f"    +{len(diffs) - examples} more")
    out.append("")
    out.append("identical" if report.identical else "differences found")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def archived_devices(
    db: AsyncSession, *, since: datetime, until: datetime, device: str | None
) -> list[str]:
    stmt = (
        select(HARawEvent.device_id)
        .where(
            HARawEvent.recorded_at >= since,
            HARawEvent.recorded_at <= until,
            HARawEvent.device_id.is_not(None),
        )
        .distinct()
    )
    if device is not None:
        stmt = stmt.where(HARawEvent.device_id == device)
    return sorted(str(d) for d in (await db.execute(stmt)).scalars().all())


async def run(
    *,
    live_url: str,
    scratch_url: str,
    since: datetime,
    until: datetime,
    device: str | None,
    tolerance: float,
    all_sources: bool = False,
) -> RunReport:
    """Replay the window into the scratch database and diff the five tables."""
    scratch_engine = await prepare_scratch(scratch_url, live_url)
    live_engine = make_engine(live_url)
    live_sessions = async_sessionmaker(live_engine, expire_on_commit=False)
    try:
        async with live_sessions() as live:
            devices = await archived_devices(
                live, since=since, until=until, device=device
            )

        async with AsyncSession(scratch_engine, expire_on_commit=False) as scratch:
            stats = await replay_archive(
                live_sessions, scratch, since=since, until=until, device_id=device
            )
            await scratch.commit()

        sources = None if all_sources else (REPLAY_SOURCE_SYSTEM,)
        tables: list[TableReport] = []
        async with (
            live_sessions() as live,
            AsyncSession(scratch_engine) as scratch,
        ):
            for name, spec in TABLES.items():
                live_rows = await load_rows(
                    live,
                    spec,
                    since=since,
                    until=until,
                    devices=devices,
                    sources=sources,
                )
                # The scratch holds only the replayed window, and today's path
                # stamps status and location rows at replay time (after
                # `until`), so it is read whole and the drift is reported.
                scratch_rows = await load_rows(scratch, spec)
                tables.append(
                    diff_rows(name, spec, live_rows, scratch_rows, tolerance=tolerance)
                )
    finally:
        await live_engine.dispose()
        await scratch_engine.dispose()
    return RunReport(
        since=since.isoformat(timespec="seconds"),
        until=until.isoformat(timespec="seconds"),
        devices=[short_device(d) for d in devices],
        scratch_url=scratch_url,
        tolerance=tolerance,
        replay=stats,
        tables=tables,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--since", default="7d", help="window start: 7d, 36h or ISO date"
    )
    parser.add_argument(
        "--scratch-url",
        default=None,
        help="database URL to replay into (default: a new temp SQLite file)",
    )
    parser.add_argument("--device", default=None, help="only this device_id")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        help="numeric difference treated as equal",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--examples", type=int, default=10, help="keys and diffs listed per table"
    )
    parser.add_argument(
        "--all-sources",
        action="store_true",
        help="compare live rows from every source_system, not only ha_fordpass",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from config import settings

    now = datetime.now(UTC)
    report = asyncio.run(
        run(
            live_url=settings.database_url,
            scratch_url=args.scratch_url or default_scratch_url(),
            since=parse_since(args.since, now=now),
            until=now,
            device=args.device,
            tolerance=args.tolerance,
            all_sources=args.all_sources,
        )
    )
    print(
        report.to_json() if args.json else render_text(report, examples=args.examples)
    )
    return 0 if report.identical else 1


if __name__ == "__main__":
    sys.exit(main())
