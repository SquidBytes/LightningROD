"""Data Repair: re-parse the FordConnect archive through live ingestion's rules."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from db.models.battery_status import EVBatteryStatus
from db.models.data_source_config import DataSourceConfig
from db.models.fcon_raw_sample import FconRawSample
from db.models.trip_metrics import EVTripMetrics
from tests.test_fcon import test_trip_merge as drive
from web.services.repair.ops.fcon_reparse import FconArchiveReparse
from web.services.repair.snapshot import restore_run

pytestmark = pytest.mark.db
DEVICE = drive.DEVICE


async def _trip_count(db) -> int:
    return await db.scalar(
        select(func.count(EVTripMetrics.id)).where(EVTripMetrics.device_id == DEVICE)
    )


async def _battery(db) -> EVBatteryStatus:
    return await db.scalar(
        select(EVBatteryStatus)
        .where(EVBatteryStatus.device_id == DEVICE)
        .order_by(EVBatteryStatus.recorded_at.desc())
        .limit(1)
    )


async def test_unavailable_without_fordconnect(db_session):
    op = FconArchiveReparse()
    await db_session.execute(
        DataSourceConfig.__table__.delete().where(DataSourceConfig.source_name == "fcon")
    )
    assert "not set up" in await op.unavailable_reason(db_session)
    await drive._feed_fcon(db_session)
    assert await op.unavailable_reason(db_session) is None


async def test_census_counts_responses_from_an_older_parser(db_session):
    await drive._feed_fcon(db_session)
    op = FconArchiveReparse()
    assert await op.census(db_session) == 0
    await db_session.execute(update(FconRawSample).values(ingest_schema_version=1))
    assert await op.census(db_session) == len(drive.FCON_DRIVE)


async def test_reparse_repairs_values_and_recovers_rows_then_is_idempotent(db_session):
    await drive._feed_fcon(db_session)
    battery = await _battery(db_session)
    battery.hv_battery_range = 1.0  # what a mapping bug left behind
    trip = await db_session.scalar(select(EVTripMetrics).where(EVTripMetrics.device_id == DEVICE))
    await db_session.delete(trip)
    await db_session.flush()

    op = FconArchiveReparse()
    preview = await op.preview(db_session)
    actions = {(g.diffs[0].action, g.label.split()[0 if g.diffs[0].action == "update" else 1])
               for g in preview.groups}
    assert ("update", "ev_battery_status") in actions
    assert ("insert", "ev_trip_metrics") in actions
    # Preview wrote nothing.
    assert float((await _battery(db_session)).hv_battery_range) == 1.0
    assert await _trip_count(db_session) == 0

    result = await op.apply(db_session)
    assert result.snapshot_rows >= 1
    assert float((await _battery(db_session)).hv_battery_range) == 272.5
    assert await _trip_count(db_session) == 1
    assert (await op.preview(db_session)).total == 0

    await restore_run(db_session, result.run_id)
    await db_session.flush()
    assert float((await _battery(db_session)).hv_battery_range) == 1.0


async def test_reparse_merges_into_home_assistant_trip(db_session):
    await drive._feed_ha(db_session)
    await drive._feed_fcon(db_session)
    [merged] = await drive._trips(db_session)
    before = drive._snapshot(merged)
    await FconArchiveReparse().apply(db_session)
    [after] = await drive._trips(db_session)
    assert drive._snapshot(after) == before


async def test_unselected_changes_are_undone(db_session):
    await drive._feed_fcon(db_session)
    battery = await _battery(db_session)
    battery.hv_battery_range = 1.0
    trip = await db_session.scalar(select(EVTripMetrics).where(EVTripMetrics.device_id == DEVICE))
    await db_session.delete(trip)
    await db_session.flush()
    op = FconArchiveReparse()
    keys = [g.key for g in (await op.preview(db_session)).groups if g.diffs[0].action == "update"]
    await op.apply(db_session, keys=keys)
    assert float((await _battery(db_session)).hv_battery_range) == 272.5
    assert await _trip_count(db_session) == 0
