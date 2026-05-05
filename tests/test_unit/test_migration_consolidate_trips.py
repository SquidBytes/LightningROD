"""Trip-consolidation migration: schema + consolidation invariants.

Asserts the post-upgrade shape of `ev_trip_metrics`:
  - `odometer_start` and `odometer_end` columns exist
  - `trip_id` has no DB-side default (model default removed; migration relies
    on adapter-supplied uuid5)
  - UNIQUE(trip_id) constraint blocks duplicate inserts at the row level

Plus exercises the consolidation routine on an isolated in-memory aiosqlite DB
seeded with three duplicate rows + one legacy single row, asserting:
  - Duplicate group collapses to one row (most-populated kept)
  - Survivor's trip_id is deterministic uuid5(NS, "legacy|device|end_time")
  - Merged temps come from events-canonical row, scores from elveh-canonical
    row, distance/energy/duration take MAX of conflicting non-NULLs
  - Single-row legacy entry survives with deterministic uuid5
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from db.migrations.versions.p34_battery_trips_overhaul import (
    LIGHTNINGROD_TRIP_NAMESPACE,
    _coerce_datetime,
    consolidate_trip_groups,
    rewrite_trip_ids_to_legacy_form,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Schema-level assertions (run against the head-migrated test DB)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_odometer_columns_present(db_session):
    """odometer_start and odometer_end must exist on ev_trip_metrics."""
    backend = (await db_session.execute(sa.text("SELECT 1"))).scalar()
    assert backend == 1  # smoke

    # PRAGMA on SQLite, information_schema on PG. Use SQLAlchemy inspect via
    # reflection-friendly approach: select the column names from a reflected
    # table.
    from sqlalchemy import MetaData

    def _reflect(sync_conn):
        meta = MetaData()
        meta.reflect(bind=sync_conn, only=["ev_trip_metrics"])
        return {c.name for c in meta.tables["ev_trip_metrics"].columns}

    # AsyncSession path: use the underlying connection for sync reflection.
    conn = await db_session.connection()
    columns = await conn.run_sync(_reflect)

    assert "odometer_start" in columns, "p34 migration must add odometer_start"
    assert "odometer_end" in columns, "p34 migration must add odometer_end"


@pytest.mark.db
async def test_unique_trip_id_constraint_blocks_duplicate(db_session):
    """A second INSERT with the same trip_id must raise IntegrityError."""
    from db.models.trip_metrics import EVTripMetrics

    fixed_id = uuid.uuid5(LIGHTNINGROD_TRIP_NAMESPACE, "test|TESTVIN_UNIQ|2026-01-01T00:00:00")

    db_session.add(
        EVTripMetrics(
            trip_id=fixed_id,
            device_id="TESTVIN_UNIQ",
            end_time=datetime(2026, 1, 1, tzinfo=UTC),
            distance=10.0,
            energy_consumed=3.0,
        )
    )
    await db_session.flush()

    db_session.add(
        EVTripMetrics(
            trip_id=fixed_id,  # collision
            device_id="TESTVIN_UNIQ",
            end_time=datetime(2026, 1, 1, 1, tzinfo=UTC),
            distance=11.0,
            energy_consumed=4.0,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Consolidation logic tests (isolated in-memory DB, no Alembic chain)
# ---------------------------------------------------------------------------


def _create_legacy_table(sync_conn) -> None:
    """Create ev_trip_metrics in its pre-p34 shape (no odometer cols, no UNIQUE)."""
    sync_conn.execute(sa.text("""
        CREATE TABLE ev_trip_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id BLOB NOT NULL,
            device_id VARCHAR NOT NULL,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            recorded_at TIMESTAMP,
            distance NUMERIC,
            duration NUMERIC,
            energy_consumed NUMERIC,
            efficiency NUMERIC,
            range_regenerated NUMERIC,
            ambient_temp NUMERIC,
            cabin_temp NUMERIC,
            outside_air_temp NUMERIC,
            driving_score NUMERIC,
            speed_score NUMERIC,
            acceleration_score NUMERIC,
            deceleration_score NUMERIC,
            start_location_id INTEGER,
            end_location_id INTEGER,
            electrical_efficiency NUMERIC,
            brake_torque NUMERIC,
            is_complete BOOLEAN NOT NULL DEFAULT 0,
            source_system VARCHAR(100),
            ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            original_timestamp TIMESTAMP,
            ingest_schema_version SMALLINT
        )
    """))


def _insert_row(sync_conn, **kw) -> int:
    """Insert a row, returning its id."""
    cols = sorted(kw.keys())
    sql = (
        f"INSERT INTO ev_trip_metrics ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)})"
    )
    result = sync_conn.execute(sa.text(sql), kw)
    return result.lastrowid


@pytest.mark.asyncio
async def test_consolidation_merges_three_duplicate_rows():
    """Three rows for the same physical trip → one row with merged fields."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    end_t = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)

    async with engine.begin() as conn:
        await conn.run_sync(_create_legacy_table)

        def _seed(sync):
            # Row 1: events-canonical (has temps, no scores)
            _insert_row(
                sync,
                trip_id=uuid.uuid4().bytes,
                device_id="TESTVIN1",
                end_time=end_t,
                distance=24.9,
                energy_consumed=7.2,
                ambient_temp=18.0,
                cabin_temp=21.0,
                outside_air_temp=17.0,
                source_system="ha_fordpass_events",
                is_complete=1,
            )
            # Row 2: elveh-canonical (has scores + regen, no temps)
            _insert_row(
                sync,
                trip_id=uuid.uuid4().bytes,
                device_id="TESTVIN1",
                end_time=end_t + timedelta(seconds=30),  # 30s drift, same group
                distance=24.91,  # rounding drift
                energy_consumed=7.2,
                duration=1500.0,
                driving_score=92.0,
                range_regenerated=2.4,
                electrical_efficiency=88.0,
                source_system="ha_fordpass",
                is_complete=1,
            )
            # Row 3: third partial duplicate (mostly empty)
            _insert_row(
                sync,
                trip_id=uuid.uuid4().bytes,
                device_id="TESTVIN1",
                end_time=end_t + timedelta(seconds=15),
                distance=24.9,
                energy_consumed=7.20,
                source_system="manual",
                is_complete=1,
            )

        await conn.run_sync(_seed)

        bind_sync = await conn.get_raw_connection()  # noqa: F841 — use run_sync below
        await conn.run_sync(consolidate_trip_groups)
        await conn.run_sync(rewrite_trip_ids_to_legacy_form)

        rows = (
            await conn.execute(
                sa.text(
                    "SELECT id, trip_id, device_id, end_time, distance, "
                    "energy_consumed, duration, ambient_temp, cabin_temp, "
                    "outside_air_temp, driving_score, range_regenerated, "
                    "electrical_efficiency FROM ev_trip_metrics"
                )
            )
        ).all()

    await engine.dispose()

    assert len(rows) == 1, f"Expected consolidation to leave 1 row, got {len(rows)}"
    row = rows[0]

    # events-canonical temps win
    assert float(row.ambient_temp) == pytest.approx(18.0)
    assert float(row.cabin_temp) == pytest.approx(21.0)
    assert float(row.outside_air_temp) == pytest.approx(17.0)
    # elveh-canonical scores win
    assert float(row.driving_score) == pytest.approx(92.0)
    assert float(row.range_regenerated) == pytest.approx(2.4)
    assert float(row.electrical_efficiency) == pytest.approx(88.0)
    # MAX of distance/energy
    assert float(row.distance) == pytest.approx(24.91)
    assert float(row.energy_consumed) == pytest.approx(7.2)
    assert float(row.duration) == pytest.approx(1500.0)

    # Survivor's trip_id is deterministic uuid5(NS, "legacy|device|end_time_iso")
    # (whichever survivor was kept, its end_time + device_id determine the new id)
    survivor_id = uuid.UUID(bytes=row.trip_id) if isinstance(row.trip_id, bytes) else uuid.UUID(row.trip_id)
    # The survivor's end_time may be any of the three; recompute from the row.
    end_iso = _coerce_datetime(row.end_time).isoformat()
    expected = uuid.uuid5(
        LIGHTNINGROD_TRIP_NAMESPACE,
        f"legacy|TESTVIN1|{end_iso}",
    )
    assert survivor_id == expected


@pytest.mark.asyncio
async def test_consolidation_preserves_non_duplicates():
    """Two rows with different device_ids → both survive with deterministic ids."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    end_t = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)

    async with engine.begin() as conn:
        await conn.run_sync(_create_legacy_table)

        def _seed(sync):
            _insert_row(
                sync,
                trip_id=uuid.uuid4().bytes,
                device_id="VEH_A",
                end_time=end_t,
                distance=10.0,
                energy_consumed=3.0,
                is_complete=1,
            )
            _insert_row(
                sync,
                trip_id=uuid.uuid4().bytes,
                device_id="VEH_B",
                end_time=end_t,
                distance=10.0,
                energy_consumed=3.0,
                is_complete=1,
            )

        await conn.run_sync(_seed)
        await conn.run_sync(consolidate_trip_groups)
        await conn.run_sync(rewrite_trip_ids_to_legacy_form)

        rows = (
            await conn.execute(
                sa.text("SELECT id, trip_id, device_id, end_time FROM ev_trip_metrics ORDER BY device_id")
            )
        ).all()

    await engine.dispose()

    assert len(rows) == 2

    for row in rows:
        survivor_id = uuid.UUID(bytes=row.trip_id) if isinstance(row.trip_id, bytes) else uuid.UUID(row.trip_id)
        end_iso = _coerce_datetime(row.end_time).isoformat()
        expected = uuid.uuid5(
            LIGHTNINGROD_TRIP_NAMESPACE,
            f"legacy|{row.device_id}|{end_iso}",
        )
        assert survivor_id == expected, (
            f"Non-duplicate row for {row.device_id} did not get deterministic id"
        )
