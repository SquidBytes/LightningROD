"""Re-parse the FordConnect raw archive through today's parser and derivations.

The archive keeps every distinct FordConnect response so a mapping fix can be
applied to past data. Replaying goes through the same adapter as live
ingestion, merges included, so it can refresh or add rows but never duplicate
a trip or charge another source already recorded.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import event, func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.data_source_config import DataSourceConfig
from db.models.fcon_raw_sample import FconRawSample
from db.models.trip_metrics import EVTripMetrics
from web.services.repair.base import (
    DEFAULT_PREVIEW_LIMIT,
    RepairDiff,
    RepairGroup,
    RepairOperation,
    RepairPreview,
    RepairResult,
    scratch_session,
)
from web.services.source_merge import MERGEABLE_SOURCES

# Tables the FordConnect adapter writes; anything else it touches is a bug.
_SCOPE = (
    "ev_battery_status",
    "ev_vehicle_status",
    "ev_charging_session",
    "ev_trip_metrics",
    "ev_location",
    "ev_vehicles",
)
_PARSED_ENDPOINTS = ("garage", "telemetry")
# Columns that change on every write without carrying data.
_IGNORED = {"ingested_at", "recorded_at", "updated_at"}
# The natural time that names an inserted row across runs.
_INSERT_TIME = {
    "ev_charging_session": "session_start_utc",
    "ev_trip_metrics": "start_time",
}


def _norm(value: Any) -> Any:
    """Comparable form: numbers as floats, timestamps UTC-aware."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (Decimal, int, float)):
        return float(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _same(a: Any, b: Any) -> bool:
    a, b = _norm(a), _norm(b)
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


class _ChangeLog:
    """Records every insert and column change the replay flushes, per row."""

    def __init__(self, session: AsyncSession) -> None:
        self._sync = session.sync_session
        self.inserted: list[Any] = []
        self._inserted_ids: set[int] = set()
        # (table, pk) -> (row, {column: [first old, latest new]})
        self.updated: dict[tuple[str, Any], tuple[Any, dict[str, list[Any]]]] = {}

    def __enter__(self) -> _ChangeLog:
        event.listen(self._sync, "before_flush", self._on_flush)
        return self

    def __exit__(self, *exc_info: object) -> None:
        event.remove(self._sync, "before_flush", self._on_flush)

    def _on_flush(self, session, _ctx, _instances) -> None:
        for obj in session.new:
            if getattr(obj, "__tablename__", None) in _SCOPE and id(obj) not in self._inserted_ids:
                self._inserted_ids.add(id(obj))
                self.inserted.append(obj)
        for obj in session.dirty:
            table = getattr(obj, "__tablename__", None)
            if table not in _SCOPE or id(obj) in self._inserted_ids:
                continue
            state = inspect(obj)
            _, fields = self.updated.setdefault((table, state.identity), (obj, {}))
            for attr in state.mapper.column_attrs:
                if attr.key in _IGNORED:
                    continue
                history = state.attrs[attr.key].history
                if not history.has_changes():
                    continue
                old = history.deleted[0] if history.deleted else None
                new = history.added[0] if history.added else None
                if attr.key in fields:
                    fields[attr.key][1] = new
                else:
                    fields[attr.key] = [old, new]


def _insert_key(obj: Any) -> str:
    table = obj.__tablename__
    when = getattr(obj, _INSERT_TIME.get(table, "recorded_at"), None)
    when = _norm(when).isoformat() if isinstance(when, datetime) else when
    return f"new:{table}:{getattr(obj, 'device_id', None)}:{when}"


def _row_image(obj: Any) -> dict[str, Any]:
    return {
        attr.key: _norm(getattr(obj, attr.key))
        for attr in inspect(obj).mapper.column_attrs
        if attr.key not in _IGNORED and getattr(obj, attr.key) is not None
    }


class FconArchiveReparse(RepairOperation):
    """Re-run the FordConnect parser, merges and derivations over the raw archive."""

    slug = "fcon-archive-reparse"
    display_name = "FordConnect archive re-parse"
    description = (
        "Re-reads every archived FordConnect response through today's parser, "
        "charge-session and trip derivation, refreshing what a mapping fix "
        "changed and adding anything that was missed. Trips and charges Home "
        "Assistant also recorded are merged, never duplicated."
    )
    model = EVTripMetrics
    guarded_source_systems = MERGEABLE_SOURCES

    def __init__(self) -> None:
        self.last_details: dict[str, Any] = {}

    async def unavailable_reason(self, db: AsyncSession) -> str | None:
        configured = await db.scalar(
            select(func.count(DataSourceConfig.id)).where(DataSourceConfig.source_name == "fcon")
        )
        archived = await db.scalar(select(func.count(FconRawSample.id)))
        if not configured and not archived:
            return "FordConnect is not set up, so there is no archive to re-parse."
        if not archived:
            return "The FordConnect archive is empty so far."
        return None

    @staticmethod
    def _current_version() -> int:
        from web.services.sources.fcon.raw_store import INGEST_SCHEMA_VERSION

        return INGEST_SCHEMA_VERSION

    async def census(self, db: AsyncSession) -> int:
        """Archived responses parsed by an older parser version."""
        return int(
            await db.scalar(
                select(func.count(FconRawSample.id)).where(
                    FconRawSample.endpoint.in_(_PARSED_ENDPOINTS),
                    func.coalesce(FconRawSample.ingest_schema_version, 0)
                    < self._current_version(),
                )
            )
            or 0
        )

    async def _replay(self, db: AsyncSession) -> _ChangeLog:
        from web.services.sources.fcon import adapter

        samples = (
            await db.execute(
                select(FconRawSample.config_id, FconRawSample.endpoint, FconRawSample.payload)
                .where(FconRawSample.endpoint.in_(_PARSED_ENDPOINTS))
                .order_by(FconRawSample.recorded_at, FconRawSample.id)
            )
        ).all()
        with _ChangeLog(db) as log:
            for config_id, endpoint, payload in samples:
                await adapter.process_sample(endpoint, payload, db, {"config_id": config_id})
            await db.flush()
        self.last_details = {"responses_replayed": len(samples)}
        return log

    @staticmethod
    def _groups(log: _ChangeLog) -> list[tuple[RepairGroup, Any, dict[str, list[Any]] | None]]:
        """(group, row, changed fields or None for an insert), in a stable order."""
        out: list[tuple[RepairGroup, Any, dict[str, list[Any]] | None]] = []
        for (table, identity), (row, fields) in sorted(
            log.updated.items(), key=lambda item: (item[0][0], str(item[0][1]))
        ):
            changed = {k: v for k, v in fields.items() if not _same(v[0], v[1])}
            if not changed:
                continue
            key = f"{table}:{identity[0] if identity else row.id}"
            diff = RepairDiff(
                row.id,
                {k: _norm(v[0]) for k, v in changed.items()},
                {k: _norm(v[1]) for k, v in changed.items()},
                "update",
            )
            out.append((RepairGroup([diff], label=f"{table} #{row.id}", key=key), row, changed))
        for row in sorted(log.inserted, key=_insert_key):
            diff = RepairDiff(row.id, None, _row_image(row), "insert")
            group = RepairGroup(
                [diff],
                label=f"New {row.__tablename__} row",
                context={"recovered": "no row existed for this reading before the re-parse"},
                key=_insert_key(row),
            )
            out.append((group, row, None))
        return out

    async def preview(
        self, db: AsyncSession, limit: int = DEFAULT_PREVIEW_LIMIT, offset: int = 0
    ) -> RepairPreview:
        async with scratch_session(db) as session:
            groups = [g for g, _, _ in self._groups(await self._replay(session))]
        return RepairPreview(groups[offset : offset + limit], len(groups), offset, limit)

    async def affected_rows(
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> list[Any]:
        """Stored rows the re-parse would update, found by a dry run."""
        async with scratch_session(db) as session:
            planned = [
                (row.__class__, row.id, group.key)
                for group, row, changed in self._groups(await self._replay(session))
                if changed is not None
            ]
        selection = await self.selection(db, keys)
        rows = []
        for model, pk, key in planned:
            if selection.allows(key):
                row = await db.get(model, pk)
                if row is not None:
                    rows.append(row)
        return rows

    async def execute(self, db: AsyncSession, keys: Iterable[str] | None = None) -> int:
        """Replay for real, then undo whatever falls outside the selection."""
        selection = await self.selection(db, keys)
        changed_count = 0
        for group, row, changed in self._groups(await self._replay(db)):
            if selection.allows(group.key):
                changed_count += 1
            elif changed is None:
                await db.delete(row)
            else:
                for column, (old, _new) in changed.items():
                    setattr(row, column, old)
        if keys is None and not selection.skipped:
            # Everything was re-parsed under the current parser.
            await db.execute(
                update(FconRawSample)
                .where(FconRawSample.endpoint.in_(_PARSED_ENDPOINTS))
                .values(ingest_schema_version=self._current_version())
            )
        await db.flush()
        self.last_details["rows_changed"] = changed_count
        return changed_count

    async def apply(
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> RepairResult:
        """Snapshot the rows it will update, then re-parse even when there are none.

        Inserted rows are not snapshotted, like the replay operations: a
        restore puts updated rows back, and re-running re-derives inserts.
        """
        import uuid

        from web.services.repair.snapshot import RESTORABLE_MODELS, snapshot_rows

        keys = None if keys is None else frozenset(keys)
        rows = [
            r for r in await self.affected_rows(db, keys)
            if r.__tablename__ in RESTORABLE_MODELS
        ]
        run_id = None
        count = 0
        if rows:
            self.guard(rows)
            run_id = uuid.uuid4()
            count = await snapshot_rows(db, run_id, self.slug, rows)
        affected = await self.execute(db, keys)
        return RepairResult(self.slug, run_id, affected, count, details=dict(self.last_details))
