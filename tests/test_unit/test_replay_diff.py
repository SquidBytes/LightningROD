"""Replay-and-diff harness: archived events land in a scratch database, live stays untouched."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.location import EVLocation
from db.models.raw_event import HARawEvent
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle
from db.models.vehicle_status import EVVehicleStatus
from scripts import replay_diff
from tests.conftest import TEST_DB_URL, FixedSessionFactory, _attach_sqlite_pragmas
from tests.factories.raw_events import METRIC_UNIT_SYSTEM, RawEventFactory
from web.services.repair.replay_core import ReplayStats, replay_archive

pytestmark = pytest.mark.unit

VIN = "TESTVIN001"
TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
WINDOW = (TS - timedelta(hours=2), TS + timedelta(hours=1))
THREE_EVENTS = ("events", "metrics", "energytransferlogentry")
COUNTED = (
    EVBatteryStatus,
    EVVehicleStatus,
    EVLocation,
    EVTripMetrics,
    EVChargingSession,
    HARawEvent,
)


@pytest.fixture(autouse=True)
def _clear_ingestion_state():
    from web.services.repair.replay_core import purge_pending_state
    from web.services.sources.ha_fordpass.adapter import _last_seen_raw
    from web.services.units import detection

    def _clear():
        purge_pending_state()
        _last_seen_raw.clear()
        detection._records.clear()
        detection._recent_reads.clear()

    _clear()
    yield
    _clear()


async def _counts(db: AsyncSession) -> dict[str, int]:
    return {
        model.__tablename__: await db.scalar(select(func.count()).select_from(model))
        for model in COUNTED
    }


async def _seed_three_events(db: AsyncSession) -> None:
    for suffix in THREE_EVENTS:
        await RawEventFactory.create(
            db,
            suffix=suffix,
            device_id=VIN,
            recorded_at=TS,
            ha_unit_system=METRIC_UNIT_SYSTEM,
        )


def _scratch_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'scratch.db'}"


@pytest.mark.db
async def test_three_events_fill_scratch_and_leave_live_untouched(db_session, tmp_path):
    await _seed_three_events(db_session)
    before = await _counts(db_session)

    engine = await replay_diff.prepare_scratch(_scratch_url(tmp_path), TEST_DB_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as scratch:
            stats = await replay_archive(
                FixedSessionFactory(db_session),
                scratch,
                since=WINDOW[0],
                until=WINDOW[1],
            )
            await scratch.commit()
        async with AsyncSession(engine) as scratch:
            scratch_counts = await _counts(scratch)
    finally:
        await engine.dispose()

    assert stats == ReplayStats(replayed=3)
    assert scratch_counts == {
        "ev_trip_metrics": 1,
        "ev_battery_status": 1,
        "ev_charging_session": 1,
        "ev_vehicle_status": 0,
        "ev_location": 0,
        "ha_raw_events": 0,
    }
    assert await _counts(db_session) == before
    assert before["ha_raw_events"] == 3


@pytest.mark.db
async def test_metrics_without_a_unit_system_are_skipped(db_session, tmp_path):
    await RawEventFactory.create(
        db_session, suffix="metrics", device_id=VIN, recorded_at=TS, ha_unit_system=None
    )
    await RawEventFactory.create(
        db_session, suffix="events", device_id=VIN, recorded_at=TS, ha_unit_system=None
    )

    engine = await replay_diff.prepare_scratch(_scratch_url(tmp_path), TEST_DB_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as scratch:
            stats = await replay_archive(FixedSessionFactory(db_session), scratch)
    finally:
        await engine.dispose()

    assert stats == ReplayStats(replayed=1, skipped_unknown_units=1)


@pytest.mark.db
async def test_run_reports_scratch_only_rows_without_full_device_id(tmp_path):
    """End to end on committed rows: live has only the archive, so every derived row is scratch-only."""
    engine = create_async_engine(TEST_DB_URL)
    _attach_sqlite_pragmas(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            await _seed_three_events(db)
            await db.commit()
            before = await _counts(db)

        report = await replay_diff.run(
            live_url=TEST_DB_URL,
            scratch_url=_scratch_url(tmp_path),
            since=WINDOW[0],
            until=WINDOW[1],
            device=VIN,
            tolerance=0.01,
        )

        async with sessions() as db:
            assert await _counts(db) == before
    finally:
        async with sessions() as db:
            for model in (HARawEvent, EVVehicle):
                await db.execute(delete(model).where(model.device_id == VIN))
            await db.commit()
        await engine.dispose()

    by_table = {t.table: t for t in report.tables}
    assert not report.identical
    assert by_table["ev_trip_metrics"].scratch_count == 1
    assert by_table["ev_trip_metrics"].live_count == 0
    assert len(by_table["ev_charging_session"].scratch_only) == 1
    assert report.devices == ["…N001"]

    text = replay_diff.render_text(report, examples=5)
    assert VIN not in text
    assert "…N001" in text
    assert "ev_battery_status" in text and "scratch-only" in text
    data = json.loads(report.to_json())
    assert data["identical"] is False
    assert data["replay"]["replayed"] == 3
    assert VIN not in report.to_json()


def test_scratch_url_must_differ_from_live(tmp_path):
    live = f"sqlite+aiosqlite:///{tmp_path / 'live.db'}"
    with pytest.raises(SystemExit):
        replay_diff.assert_scratch_is_not_live(live, live)
    assert replay_diff.same_database(
        "postgresql+asyncpg://a:b@db:5432/lr", "postgresql+asyncpg://x:y@db:5432/lr"
    )
    assert not replay_diff.same_database(
        "postgresql+asyncpg://a:b@db:5432/lr", "postgresql+asyncpg://a:b@db:5432/other"
    )
    replay_diff.assert_scratch_is_not_live(
        f"sqlite+aiosqlite:///{tmp_path / 's.db'}", live
    )


def test_parse_since_and_tolerance():
    now = datetime(2026, 11, 7, tzinfo=UTC)
    assert replay_diff.parse_since("7d", now=now) == now - timedelta(days=7)
    assert replay_diff.parse_since("36h", now=now) == now - timedelta(hours=36)
    assert replay_diff.parse_since("2026-11-01", now=now) == datetime(
        2026, 11, 1, tzinfo=UTC
    )
    assert replay_diff.values_equal(1.004, 1.0, 0.01)
    assert not replay_diff.values_equal(1.02, 1.0, 0.01)
    assert replay_diff.values_equal(None, None, 0.01)
    assert not replay_diff.values_equal(None, 0.0, 0.01)
