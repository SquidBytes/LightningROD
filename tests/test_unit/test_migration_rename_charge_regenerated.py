"""Trip snapshots taken before the rename still restore after it."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from db.migrations.versions.rename_charge_regenerated import rename_snapshot_key
from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from web.services.repair.snapshot import restore_run

pytestmark = [pytest.mark.unit, pytest.mark.db]


async def test_old_trip_snapshot_restores_charge_regenerated(db_session):
    trip = await TripFactory.create(db_session, device_id="SNAP_VIN", charge_regenerated=None)
    run_id = uuid.uuid4()
    db_session.add_all([
        RepairBackup(run_id=run_id, operation="op", table_name="ev_trip_metrics",
                     row_pk=trip.id, row_json={"driving_score": 42.0}),
        RepairBackup(run_id=run_id, operation="op", table_name="ev_charging_session",
                     row_pk=999_999, row_json={"driving_score": 1.0}),
    ])
    await db_session.flush()

    await db_session.run_sync(
        lambda s: rename_snapshot_key(s.connection(), "driving_score", "charge_regenerated")
    )
    db_session.expire_all()

    rows = dict((await db_session.execute(
        select(RepairBackup.table_name, RepairBackup.row_json).where(RepairBackup.run_id == run_id)
    )).all())
    assert rows == {
        "ev_trip_metrics": {"charge_regenerated": 42.0},
        "ev_charging_session": {"driving_score": 1.0},
    }

    await db_session.execute(
        RepairBackup.__table__.delete().where(RepairBackup.table_name == "ev_charging_session")
    )
    await restore_run(db_session, run_id)
    assert await db_session.scalar(
        select(EVTripMetrics.charge_regenerated).where(EVTripMetrics.id == trip.id)
    ) == pytest.approx(42.0)
