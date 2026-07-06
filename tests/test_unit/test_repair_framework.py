"""Repair framework core: schema, serialization, apply/restore/purge cycle, guards.

Covers WP1 of the data-repair capability:
  - repair_backup table exists post-migration with the expected columns
  - serialize_row/deserialize_row round-trip Decimal/datetime/UUID/None
  - a dummy RepairOperation walks apply -> snapshot -> restore -> purge,
    and a second apply() is a no-op
  - mutable_only excludes manual_entry/csv_import rows
  - rollback_session discards work committed inside the context
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import MetaData, func, select
from sqlalchemy.ext.asyncio import create_async_engine

from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair import (
    RepairOperation,
    deserialize_row,
    mutable_only,
    purge_run,
    restore_run,
    rollback_session,
    serialize_row,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_repair_backup_table_schema(db_session):
    """repair_backup exists after migration with the expected columns."""

    def _reflect(sync_conn):
        meta = MetaData()
        meta.reflect(bind=sync_conn, only=["repair_backup"])
        return {c.name for c in meta.tables["repair_backup"].columns}

    conn = await db_session.connection()
    columns = await conn.run_sync(_reflect)

    assert columns == {
        "id",
        "run_id",
        "operation",
        "table_name",
        "row_pk",
        "row_json",
        "created_at",
    }


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_serialize_deserialize_roundtrip(db_session):
    """Decimal, tz-aware datetime, UUID, and None survive the round-trip."""
    vehicle = await VehicleFactory.create(db_session)
    end_time = datetime(2026, 6, 1, 12, 30, 15, tzinfo=UTC)
    trip = await TripFactory.create(
        db_session,
        device_id=vehicle.device_id,
        source_system="ha_fordpass",
        distance=Decimal("42.55"),
        end_time=end_time,
        start_time=None,
        ambient_temp=None,
    )

    data = serialize_row(trip)
    json.dumps(data)  # every value must be JSON-safe

    assert data["trip_id"] == str(trip.trip_id)
    assert data["end_time"] == end_time.isoformat()
    assert data["distance"] == pytest.approx(42.55)
    assert data["start_time"] is None

    restored = deserialize_row(EVTripMetrics, data)
    assert restored["trip_id"] == trip.trip_id
    assert restored["end_time"] == end_time
    assert restored["distance"] == pytest.approx(42.55)
    assert restored["start_time"] is None
    assert restored["ambient_temp"] is None


# ---------------------------------------------------------------------------
# Dummy operation: apply -> snapshot -> restore -> purge
# ---------------------------------------------------------------------------


class _DummyDistanceRepair(RepairOperation):
    """Test-only op: caps distance on one row, deletes the rest."""

    slug = "dummy-distance-repair"
    display_name = "Dummy distance repair"
    description = "Test-only operation over ev_trip_metrics."
    model = EVTripMetrics

    def __init__(self, device_id: str):
        self.device_id = device_id

    async def _candidates(self, db):
        stmt = (
            select(EVTripMetrics)
            .where(
                mutable_only(EVTripMetrics),
                EVTripMetrics.device_id == self.device_id,
                EVTripMetrics.distance > 100,
            )
            .order_by(EVTripMetrics.id)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def census(self, db):
        return len(await self._candidates(db))

    async def preview(self, db, limit=10):
        return []

    async def affected_rows(self, db):
        return await self._candidates(db)

    async def execute(self, db):
        rows = await self._candidates(db)
        if not rows:
            return 0
        first, *rest = rows
        first.distance = 50  # UPDATE path
        for row in rest:
            await db.delete(row)  # DELETE path
        await db.flush()
        return len(rows)


@pytest.mark.db
async def test_dummy_operation_apply_restore_purge_cycle(db_session):
    """apply snapshots under one run_id; restore reverses; purge removes only that run."""
    device = "REPAIR_DUMMY_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    trip_a = await TripFactory.create(
        db_session, device_id=device, source_system="ha_fordpass", distance=500.0
    )
    trip_b = await TripFactory.create(
        db_session, device_id=device, source_system="ha_fordpass", distance=600.0
    )
    b_id, b_trip_id = trip_b.id, trip_b.trip_id

    op = _DummyDistanceRepair(device)
    result = await op.apply(db_session)

    assert result.operation == op.slug
    assert result.run_id is not None
    assert result.affected == 2
    assert result.snapshot_rows == 2

    backups = (
        (
            await db_session.execute(
                select(RepairBackup).where(RepairBackup.run_id == result.run_id)
            )
        )
        .scalars()
        .all()
    )
    assert {b.row_pk for b in backups} == {trip_a.id, b_id}
    assert all(b.operation == op.slug for b in backups)
    assert all(b.table_name == "ev_trip_metrics" for b in backups)

    # Mutation landed: trip_a updated, trip_b deleted.
    assert float(trip_a.distance) == pytest.approx(50.0)
    assert await db_session.get(EVTripMetrics, b_id) is None

    # Idempotency: nothing left to repair.
    second = await op.apply(db_session)
    assert second.run_id is None
    assert second.affected == 0
    assert second.snapshot_rows == 0

    # Restore reverses the UPDATE and re-inserts the DELETEd row.
    restored = await restore_run(db_session, result.run_id)
    assert restored == 2
    row_a = await db_session.get(EVTripMetrics, trip_a.id)
    assert float(row_a.distance) == pytest.approx(500.0)
    row_b = await db_session.get(EVTripMetrics, b_id)
    assert row_b is not None
    assert row_b.trip_id == b_trip_id
    assert float(row_b.distance) == pytest.approx(600.0)

    # Purge removes exactly this run's snapshot rows.
    other = RepairBackup(
        run_id=uuid.uuid4(),
        operation="other-op",
        table_name="ev_trip_metrics",
        row_pk=999_999,
        row_json={"id": 999_999},
    )
    db_session.add(other)
    await db_session.flush()

    purged = await purge_run(db_session, result.run_id)
    assert purged == 2
    remaining = (await db_session.execute(select(RepairBackup))).scalars().all()
    assert [b.run_id for b in remaining] == [other.run_id]


# ---------------------------------------------------------------------------
# Source-system guard
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_mutable_only_excludes_manual_and_csv_rows(db_session):
    """mutable_only filters out csv_import and manual_entry rows."""
    device = "MUTABLE_GUARD_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    for source in ("csv_import", "manual_entry", "ha_fordpass"):
        await TripFactory.create(db_session, device_id=device, source_system=source)

    stmt = select(EVTripMetrics).where(
        EVTripMetrics.device_id == device, mutable_only(EVTripMetrics)
    )
    rows = (await db_session.execute(stmt)).scalars().all()

    assert [r.source_system for r in rows] == ["ha_fordpass"]


# ---------------------------------------------------------------------------
# Dry-run session
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_rollback_session_discards_internal_commit(db_session):
    """A session.commit() inside rollback_session never persists."""
    from tests.conftest import TEST_DB_URL, _attach_sqlite_pragmas

    engine = create_async_engine(TEST_DB_URL)
    _attach_sqlite_pragmas(engine)
    device = f"ROLLBACK_VIN_{uuid.uuid4().hex[:8]}"
    try:
        async with rollback_session(engine=engine) as session:
            session.add(
                EVTripMetrics(
                    trip_id=uuid.uuid4(),
                    device_id=device,
                    end_time=datetime(2026, 6, 1, tzinfo=UTC),
                    distance=10.0,
                )
            )
            await session.commit()  # handler-style commit becomes a savepoint
            visible = await session.scalar(
                select(func.count())
                .select_from(EVTripMetrics)
                .where(EVTripMetrics.device_id == device)
            )
            assert visible == 1

        async with engine.connect() as conn:
            persisted = await conn.scalar(
                select(func.count())
                .select_from(EVTripMetrics)
                .where(EVTripMetrics.device_id == device)
            )
        assert persisted == 0
    finally:
        await engine.dispose()
