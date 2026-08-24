"""ArchiveReplay: window, state fetch, enrichment, idempotence, preservation.

Mirrors the recorder-replay suite, but the states come from `ha_raw_events`
instead of Home Assistant's recorder — so nothing here needs a runtime.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from db.models.raw_event import HARawEvent
from db.models.trip_metrics import EVTripMetrics
from tests.factories.raw_events import RawEventFactory
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair.ops.archive_replay import ArchiveReplay
from web.services.repair.snapshot import serialize_row

pytestmark = pytest.mark.unit

VIN = "TESTVIN001"
STATE_TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


class _SessionFactory:
    """Hands the op the test session instead of opening a production one."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_):
        return False


@pytest.fixture
def op(db_session):
    """An ArchiveReplay reading the archive through the test session."""
    return ArchiveReplay(session_factory=_SessionFactory(db_session))


@pytest.fixture(autouse=True)
def _clear_ingestion_state():
    """Reset handler pending caches and unit-detection caches around each test."""
    from web.services.sources.ha_fordpass import handlers as fp
    from web.services.sources.ha_fordpass.adapter import _last_seen_raw
    from web.services.units import detection

    def _clear():
        fp._last_trip_values.clear()
        fp._pending_vehicle_status.clear()
        fp._pending_vehicle_status_ts.clear()
        fp._pending_battery_status.clear()
        fp._pending_battery_status_ts.clear()
        _last_seen_raw.clear()
        detection._records.clear()
        detection._recent_reads.clear()

    _clear()
    yield
    _clear()


async def _seed_trip_entities(db_session, ts: datetime = STATE_TS) -> None:
    """One archived state per trip entity, all at the same timestamp."""
    for suffix in ArchiveReplay.TRIP_ENTITY_SUFFIXES:
        await RawEventFactory.create(
            db_session, suffix=suffix, device_id=VIN, recorded_at=ts
        )


async def _trip_count(db_session) -> int:
    return await db_session.scalar(
        select(func.count())
        .select_from(EVTripMetrics)
        .where(EVTripMetrics.device_id == VIN)
    )


# ---------------------------------------------------------------------------
# Window + state fetch
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_recorder_window_is_the_earliest_archived_events_row(op, db_session):
    """The window opens at the oldest archived trip-events state, tz-aware."""
    await RawEventFactory.create(
        db_session, suffix="events", device_id=VIN, recorded_at=STATE_TS
    )
    await RawEventFactory.create(
        db_session,
        suffix="events",
        device_id=VIN,
        recorded_at=STATE_TS + timedelta(days=3),
    )

    window = await op.recorder_window()

    assert window is not None
    assert window.tzinfo is not None
    assert window.replace(tzinfo=None) == STATE_TS.replace(tzinfo=None)


@pytest.mark.db
async def test_empty_archive_gives_no_window_and_no_states(op, db_session):
    assert await op.recorder_window() is None
    assert await op._fetch_states() == []
    assert await op.census(db_session) == 0


@pytest.mark.db
async def test_fetch_states_returns_trip_slugs_ascending(op, db_session):
    """Only trip entities come back, ordered by the time they were recorded."""
    await RawEventFactory.create(
        db_session, suffix="events", device_id=VIN, recorded_at=STATE_TS
    )
    await RawEventFactory.create(
        db_session,
        suffix="elveh",
        device_id=VIN,
        recorded_at=STATE_TS + timedelta(hours=2),
    )
    await RawEventFactory.create(
        db_session,
        suffix="metrics",
        device_id=VIN,
        recorded_at=STATE_TS + timedelta(hours=1),
    )
    # Out of scope: not a trip entity.
    await RawEventFactory.create(
        db_session, suffix="soc", device_id=VIN, recorded_at=STATE_TS
    )

    states = await op._fetch_states()

    assert [entity_id.rsplit("_", 1)[-1] for _, entity_id, _ in states] == [
        "events",
        "metrics",
        "elveh",
    ]
    timestamps = [ts for ts, _, _ in states]
    assert timestamps == sorted(timestamps)
    assert all(isinstance(payload, dict) for _, _, payload in states)


# ---------------------------------------------------------------------------
# The premise: archived events re-derive the fields mapping dropped
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_replay_fills_duration_and_scores_from_the_archive(op, db_session):
    """Seeded archive rows enrich a trip that ingestion left incomplete."""
    await VehicleFactory.create(db_session, device_id=VIN)
    trip = await TripFactory.create(
        db_session,
        device_id=VIN,
        source_system="ha_fordpass",
        distance=19.0,
        energy_consumed=7.6,
        end_time=STATE_TS,
        duration=None,
        efficiency=None,
    )
    await _seed_trip_entities(db_session)

    assert await op.census(db_session) == 1
    changed = await op.execute(db_session)

    assert changed == 1
    assert op.last_details["errors"] == 0
    assert float(trip.duration) == pytest.approx(1800.0)
    assert float(trip.driving_score) == pytest.approx(85.0)
    assert float(trip.speed_score) == pytest.approx(60.0)
    assert float(trip.range_regenerated) == pytest.approx(2.1)


@pytest.mark.db
async def test_replay_is_idempotent(op, db_session):
    """A second run over the same archive converges — no new or changed rows."""
    await VehicleFactory.create(db_session, device_id=VIN)
    await _seed_trip_entities(db_session)

    await op.execute(db_session)
    first_count = await _trip_count(db_session)

    second = await op.execute(db_session)

    assert second == 0
    assert await _trip_count(db_session) == first_count


@pytest.mark.db
async def test_csv_import_trip_is_left_byte_identical(op, db_session):
    """The preservation invariant holds: protected rows are reverted."""
    await VehicleFactory.create(db_session, device_id=VIN)
    trip = await TripFactory.create(
        db_session,
        device_id=VIN,
        source_system="csv_import",
        distance=19.0,
        energy_consumed=7.6,
        end_time=STATE_TS,
        duration=None,
    )
    before = serialize_row(trip)
    await _seed_trip_entities(db_session)

    assert await op.census(db_session) == 0  # invisible to the census
    changed = await op.execute(db_session)

    assert changed == 0
    assert op.last_details["protected_reverted"] >= 1
    assert serialize_row(trip) == before


@pytest.mark.db
async def test_apply_never_writes_back_to_the_archive(op, db_session):
    """Replay drives the slug dispatcher, which sits below the archive hook."""
    await VehicleFactory.create(db_session, device_id=VIN)
    await _seed_trip_entities(db_session)
    before = await db_session.scalar(select(func.count()).select_from(HARawEvent))

    await op.apply(db_session)

    after = await db_session.scalar(select(func.count()).select_from(HARawEvent))
    assert after == before


@pytest.mark.db
async def test_preview_persists_nothing():
    """The dry-run replays inside a rollback session and leaves no trips.

    Runs on its own engine with committed archive rows: the rollback_session
    opens a second connection, which would deadlock against the shared
    transaction the db_session fixture holds open.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from db.models.vehicle import EVVehicle
    from tests.conftest import TEST_DB_URL, _attach_sqlite_pragmas

    engine = create_async_engine(TEST_DB_URL)
    _attach_sqlite_pragmas(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            await _seed_trip_entities(db)
            await db.commit()

        op = ArchiveReplay(session_factory=sessions)
        op._rollback_engine = engine
        diffs = await op.preview(None)

        assert diffs
        assert {d.action for d in diffs} == {"insert"}
        async with sessions() as db:
            persisted = await db.scalar(
                select(func.count())
                .select_from(EVTripMetrics)
                .where(EVTripMetrics.device_id == VIN)
            )
        assert persisted == 0
    finally:
        async with sessions() as db:
            for model in (HARawEvent, EVTripMetrics, EVVehicle):
                await db.execute(delete(model).where(model.device_id == VIN))
            await db.commit()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_registered_between_telemetry_derive_and_recorder_replay():
    from web.services.repair.registry import REPAIR_REGISTRY, get_operation

    slugs = [op.slug for op in REPAIR_REGISTRY]
    assert slugs.index("archive-replay") == slugs.index("telemetry-derive") + 1
    assert slugs.index("archive-replay") == slugs.index("recorder-replay") - 1
    assert get_operation("archive-replay").runs_when_clean is True
