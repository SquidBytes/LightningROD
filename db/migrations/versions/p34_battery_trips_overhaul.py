"""Battery + trip-data overhaul: deterministic trip_id, dedup consolidation, odometer cols.

- Add ev_trip_metrics.odometer_start, odometer_end (derived at write-time
  from ev_vehicle_status.odometer; not a direct HA-source field).
- Drop random-uuid4 default on ev_trip_metrics.trip_id; rewrite existing rows
  to deterministic uuid5(NS, "legacy|device_id|end_time"); add UNIQUE
  constraint on trip_id.
- Consolidate duplicate trip rows by grouping on (device_id,
  end_time-bucket-minute, distance rounded to 1 decimal). For each group with
  >1 rows: keep the most-populated survivor and merge fields per source-canonical
  precedence (events-canonical for temps, elveh-canonical for scores/regen,
  MAX for distance/energy/duration). Loser rows are deleted.

Cross-dialect: op.batch_alter_table for the SQLite-incompatible
ALTER ... ADD CONSTRAINT path. Consolidation runs as Python over
`op.get_bind()` rows so the SQL stays portable.

Revision ID: p34_battery_trips_overhaul
Revises: p33_ice_vehicles_and_unit_policy
Create Date: 2026-05-05
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision: str = "p34_battery_trips_overhaul"
down_revision: str | Sequence[str] | None = "p33_ice_vehicles_and_unit_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Trip-id namespace (LOCKED — do not change)
# ---------------------------------------------------------------------------
# Changing this UUID would invalidate every existing deterministic trip_id and
# break the dedup invariant. The same constant lives in
# web/services/sources/ha_fordpass/adapter.py for runtime use.
LIGHTNINGROD_TRIP_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-4a5b-9c8d-7e6f5a4b3c20")


# Source-precedence rules for consolidation merges.
_TEMP_FIELDS: tuple[str, ...] = ("ambient_temp", "cabin_temp", "outside_air_temp")
_SCORE_FIELDS: tuple[str, ...] = (
    "driving_score",
    "speed_score",
    "acceleration_score",
    "deceleration_score",
    "electrical_efficiency",
    "range_regenerated",
    "brake_torque",
)
_MAX_FIELDS: tuple[str, ...] = ("distance", "energy_consumed", "duration", "efficiency")
# Columns we read off each row when consolidating. Keep this list in sync with
# anything we write back via UPDATE.
_CONSOLIDATION_COLUMNS: tuple[str, ...] = (
    "id",
    "trip_id",
    "device_id",
    "start_time",
    "end_time",
    "recorded_at",
    "distance",
    "duration",
    "energy_consumed",
    "efficiency",
    "range_regenerated",
    "ambient_temp",
    "cabin_temp",
    "outside_air_temp",
    "driving_score",
    "speed_score",
    "acceleration_score",
    "deceleration_score",
    "electrical_efficiency",
    "brake_torque",
    "source_system",
)


_logger = logging.getLogger("alembic.p34_consolidate_trips")


def _coerce_datetime(val: Any):
    """Best-effort datetime parse. SQLite returns TIMESTAMP columns as strings;
    PG returns them as native datetime objects. Return None on parse failure."""
    from datetime import datetime as _dt

    if val is None:
        return None
    if isinstance(val, _dt):
        return val
    if isinstance(val, str):
        s = val
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return _dt.fromisoformat(s)
        except ValueError:
            return None
    return None


def _bucket_key(device_id: Any, end_time: Any, distance: Any) -> tuple[str, str, float] | None:
    """Group key: device_id + end_time bucketed to nearest minute + distance rounded to 1 dp.

    Returns None when the row lacks the fields we group on (those rows are
    treated as singletons — never merged).
    """
    if device_id is None or end_time is None or distance is None:
        return None
    ts = _coerce_datetime(end_time)
    if ts is None:
        return None
    try:
        # Sub-second + sub-minute drift across sources is the dedup target.
        bucket_dt = ts.replace(second=0, microsecond=0)
        return (str(device_id), bucket_dt.isoformat(), round(float(distance), 1))
    except (ValueError, TypeError):
        return None


def _populated_count(row: dict) -> int:
    return sum(1 for v in row.values() if v is not None)


def _is_events_canonical(row: dict) -> bool:
    """Heuristic: row that carries any of the events-canonical temp fields."""
    return any(row.get(f) is not None for f in _TEMP_FIELDS)


def _is_elveh_canonical(row: dict) -> bool:
    """Heuristic: row that carries any of the elveh-canonical score/regen fields."""
    return any(row.get(f) is not None for f in _SCORE_FIELDS)


def _max_or_none(values: list[Any]) -> Any:
    """Return MAX of non-NULL numeric values, or None when all are NULL."""
    cleaned = [float(v) for v in values if v is not None]
    return max(cleaned) if cleaned else None


def _row_to_dict(row: Any) -> dict:
    """Convert a SQLAlchemy Row into a plain dict keyed by column name."""
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def consolidate_trip_groups(sync_conn) -> None:
    """Read all ev_trip_metrics rows, group, merge, then UPDATE+DELETE survivors.

    Pure synchronous Connection API so the same routine runs from migrations
    AND from in-memory test harnesses (`engine.begin() -> conn.run_sync(...)`).
    Rows that fail to bucket (NULL device_id / end_time / distance) are left
    alone.
    """
    col_list = ", ".join(_CONSOLIDATION_COLUMNS)
    rows = sync_conn.execute(sa.text(f"SELECT {col_list} FROM ev_trip_metrics")).all()
    if not rows:
        return

    rows_dicts = [_row_to_dict(r) for r in rows]

    groups: dict[tuple[str, str, float], list[dict]] = {}
    singletons: list[dict] = []
    for row in rows_dicts:
        key = _bucket_key(row.get("device_id"), row.get("end_time"), row.get("distance"))
        if key is None:
            singletons.append(row)
            continue
        groups.setdefault(key, []).append(row)

    for key, members in groups.items():
        if len(members) < 2:
            continue

        # Pick survivor: most-populated row wins. Tie-break by lowest id (stable).
        survivor = max(
            members,
            key=lambda r: (_populated_count(r), -int(r.get("id") or 0)),
        )

        # Merge per-field according to source precedence.
        merged: dict[str, Any] = {}

        # Events-canonical wins for temps: pick from any row carrying temps.
        for field in _TEMP_FIELDS:
            events_rows = [r for r in members if _is_events_canonical(r) and r.get(field) is not None]
            if events_rows:
                merged[field] = events_rows[0][field]
            else:
                # Fall back to any non-NULL value across the group.
                for r in members:
                    if r.get(field) is not None:
                        merged[field] = r[field]
                        break

        # Elveh-canonical wins for scores/regen.
        for field in _SCORE_FIELDS:
            elveh_rows = [r for r in members if _is_elveh_canonical(r) and r.get(field) is not None]
            if elveh_rows:
                merged[field] = elveh_rows[0][field]
            else:
                for r in members:
                    if r.get(field) is not None:
                        merged[field] = r[field]
                        break

        # MAX for distance / energy / duration / efficiency (defensive against rounding drift).
        for field in _MAX_FIELDS:
            merged[field] = _max_or_none([r.get(field) for r in members])

        # Apply merged fields onto the survivor row in memory, then UPDATE.
        update_payload: dict[str, Any] = {}
        for field, val in merged.items():
            if val is not None and val != survivor.get(field):
                update_payload[field] = val

        if update_payload:
            set_clause = ", ".join(f"{k} = :{k}" for k in update_payload)
            sync_conn.execute(
                sa.text(f"UPDATE ev_trip_metrics SET {set_clause} WHERE id = :id"),
                {**update_payload, "id": survivor["id"]},
            )

        # Delete the loser rows.
        loser_ids = [r["id"] for r in members if r["id"] != survivor["id"]]
        if loser_ids:
            sync_conn.execute(
                sa.text("DELETE FROM ev_trip_metrics WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": loser_ids},
            )
            _logger.info(
                "consolidate: device=%s end_time=%s kept=%s deleted=%s",
                key[0], key[1], survivor["id"], loser_ids,
            )


def _bind_uuid_value(sync_conn, new_id: uuid.UUID) -> Any:
    """Adapt a uuid.UUID to the dialect-native binding shape.

    SQLAlchemy's Uuid(as_uuid=True) type stores UUIDs as a 32-character hex
    string (CHAR(32)) on SQLite and as the native UUID type on PostgreSQL.
    Raw `sa.text(...)` bind params bypass the typed-column dispatch, so we
    mirror the same shape manually here — anything else (e.g. raw bytes on
    SQLite) makes the column unreadable through the ORM result processor.
    """
    dialect_name = sync_conn.dialect.name
    if dialect_name == "sqlite":
        return new_id.hex
    return new_id  # asyncpg / psycopg2 accept uuid.UUID directly


def consolidate_trip_id_collisions(sync_conn) -> None:
    """Final dedup pass keyed on trip_id itself.

    The bucket-based pass in `consolidate_trip_groups` groups on
    (device_id, minute-bucketed end_time, rounded distance), but trip_id is
    derived from (device_id, full end_iso) only — so two rows with identical
    end_time but different distances slip past the bucket pass and then
    collide on trip_id, breaking the UNIQUE constraint added next. Anything
    that hashes to the same trip_id is by definition the same trip under
    the new contract; merge them with the same source-precedence rules.
    """
    col_list = ", ".join(_CONSOLIDATION_COLUMNS)
    rows = sync_conn.execute(sa.text(f"SELECT {col_list} FROM ev_trip_metrics")).all()
    if not rows:
        return

    by_trip_id: dict[Any, list[dict]] = {}
    for row in rows:
        row_d = _row_to_dict(row)
        tid = row_d.get("trip_id")
        if tid is None:
            continue
        by_trip_id.setdefault(tid, []).append(row_d)

    for tid, members in by_trip_id.items():
        if len(members) < 2:
            continue

        survivor = max(
            members,
            key=lambda r: (_populated_count(r), -int(r.get("id") or 0)),
        )

        merged: dict[str, Any] = {}
        for field in _TEMP_FIELDS:
            events_rows = [r for r in members if _is_events_canonical(r) and r.get(field) is not None]
            if events_rows:
                merged[field] = events_rows[0][field]
            else:
                for r in members:
                    if r.get(field) is not None:
                        merged[field] = r[field]
                        break
        for field in _SCORE_FIELDS:
            elveh_rows = [r for r in members if _is_elveh_canonical(r) and r.get(field) is not None]
            if elveh_rows:
                merged[field] = elveh_rows[0][field]
            else:
                for r in members:
                    if r.get(field) is not None:
                        merged[field] = r[field]
                        break
        for field in _MAX_FIELDS:
            merged[field] = _max_or_none([r.get(field) for r in members])

        update_payload: dict[str, Any] = {}
        for field, val in merged.items():
            if val is not None and val != survivor.get(field):
                update_payload[field] = val
        if update_payload:
            set_clause = ", ".join(f"{k} = :{k}" for k in update_payload)
            sync_conn.execute(
                sa.text(f"UPDATE ev_trip_metrics SET {set_clause} WHERE id = :id"),
                {**update_payload, "id": survivor["id"]},
            )

        loser_ids = [r["id"] for r in members if r["id"] != survivor["id"]]
        if loser_ids:
            sync_conn.execute(
                sa.text("DELETE FROM ev_trip_metrics WHERE id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": loser_ids},
            )
            _logger.info(
                "consolidate-by-trip-id: trip_id=%s kept=%s deleted=%s",
                tid, survivor["id"], loser_ids,
            )


def rewrite_trip_ids_to_legacy_form(sync_conn) -> None:
    """Rewrite every surviving row's trip_id to uuid5(NS, "legacy|device|end_time").

    Existing rows have random uuid4 trip_ids that won't match the deterministic
    id any future ingestion would compute. Rewriting them to a deterministic
    form (legacy prefix + recoverable inputs) makes them stable across
    re-ingest attempts AND distinct from future "real" deterministic ids
    (which use device_id|tripUpdateTime, not the legacy prefix).
    """
    rows = sync_conn.execute(
        sa.text("SELECT id, device_id, end_time FROM ev_trip_metrics")
    ).all()
    for row in rows:
        row_d = _row_to_dict(row)
        device_id = row_d.get("device_id")
        end_time = row_d.get("end_time")
        if device_id is None or end_time is None:
            # Without recoverable inputs we can't compute a stable id. Leave
            # the existing uuid4 in place — it remains globally unique even
            # though it's not "deterministic". The UNIQUE constraint added next
            # still holds.
            continue
        ts = _coerce_datetime(end_time)
        end_iso = ts.isoformat() if ts is not None else str(end_time)
        new_id = uuid.uuid5(LIGHTNINGROD_TRIP_NAMESPACE, f"legacy|{device_id}|{end_iso}")
        sync_conn.execute(
            sa.text("UPDATE ev_trip_metrics SET trip_id = :tid WHERE id = :id"),
            {"tid": _bind_uuid_value(sync_conn, new_id), "id": row_d["id"]},
        )


def upgrade() -> None:
    # Step 1: add the two new nullable columns.
    op.add_column("ev_trip_metrics", sa.Column("odometer_start", sa.Numeric(), nullable=True))
    op.add_column("ev_trip_metrics", sa.Column("odometer_end", sa.Numeric(), nullable=True))

    # Steps 2 + 3: consolidate duplicates and rewrite trip_ids. Skip the data
    # passes when running in `--sql` (offline) mode because we have no live
    # connection to read rows from — offline render captures DDL only.
    if not context.is_offline_mode():
        bind = op.get_bind()

        def _run_data_passes(sync_conn):
            consolidate_trip_groups(sync_conn)
            rewrite_trip_ids_to_legacy_form(sync_conn)
            consolidate_trip_id_collisions(sync_conn)

        # `bind` may already be a sync Connection inside Alembic's online flow.
        run_sync = getattr(bind, "run_sync", None)
        if callable(run_sync):
            run_sync(_run_data_passes)
        else:
            _run_data_passes(bind)

    # Step 4: add UNIQUE(trip_id). Cross-dialect via batch on SQLite (since
    # SQLite cannot ALTER TABLE ADD CONSTRAINT). PG handles direct add.
    bind_dialect = (
        op.get_bind().dialect.name
        if not context.is_offline_mode()
        else context.get_context().dialect.name
    )
    if bind_dialect == "sqlite" and not context.is_offline_mode():
        with op.batch_alter_table("ev_trip_metrics") as batch_op:
            batch_op.create_unique_constraint("uq_ev_trip_metrics_trip_id", ["trip_id"])
    else:
        op.create_unique_constraint(
            "uq_ev_trip_metrics_trip_id", "ev_trip_metrics", ["trip_id"]
        )


def downgrade() -> None:
    """One-way migration. Consolidation deletes losers — no per-row backup."""
    raise NotImplementedError(
        "p34_battery_trips_overhaul is a one-way migration "
        "(consolidation deletes duplicate rows; restoring them is not supported)."
    )
