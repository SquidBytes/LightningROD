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
from tests.conftest import FixedSessionFactory
from tests.factories.raw_events import (
    IMPERIAL_UNIT_SYSTEM,
    METRIC_UNIT_SYSTEM,
    RawEventFactory,
)
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair.ops.archive_replay import ArchiveReplay
from web.services.repair.snapshot import serialize_row

pytestmark = pytest.mark.unit

VIN = "TESTVIN001"
STATE_TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def op(db_session):
    """An ArchiveReplay reading the archive through the test session."""
    return ArchiveReplay(session_factory=FixedSessionFactory(db_session))


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


async def _seed_trip_entities(
    db_session,
    ts: datetime = STATE_TS,
    *,
    fixture: str = "metric_ha_metric_vehicle",
    unit_system: dict | None = METRIC_UNIT_SYSTEM,
) -> None:
    """One archived state per trip entity, all at the same timestamp."""
    for suffix in ArchiveReplay.TRIP_ENTITY_SUFFIXES:
        await RawEventFactory.create(
            db_session,
            suffix=suffix,
            device_id=VIN,
            recorded_at=ts,
            fixture=fixture,
            ha_unit_system=unit_system,
        )


async def _trips(db_session) -> list[EVTripMetrics]:
    result = await db_session.execute(
        select(EVTripMetrics)
        .where(EVTripMetrics.device_id == VIN)
        .order_by(EVTripMetrics.id)
    )
    return list(result.scalars().all())


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
        db_session,
        suffix="events",
        device_id=VIN,
        recorded_at=STATE_TS,
        ha_unit_system=METRIC_UNIT_SYSTEM,
    )
    # Captured after the user switched Home Assistant to imperial.
    await RawEventFactory.create(
        db_session,
        suffix="elveh",
        device_id=VIN,
        recorded_at=STATE_TS + timedelta(hours=2),
        ha_unit_system=IMPERIAL_UNIT_SYSTEM,
    )
    await RawEventFactory.create(
        db_session,
        suffix="metrics",
        device_id=VIN,
        recorded_at=STATE_TS + timedelta(hours=1),
        ha_unit_system=METRIC_UNIT_SYSTEM,
    )
    # Out of scope: not a trip entity.
    await RawEventFactory.create(
        db_session, suffix="soc", device_id=VIN, recorded_at=STATE_TS
    )
    # Also out of scope: the device tracker is archived for its GPS accuracy
    # and altitude, and has no trip payload to replay.
    db_session.add(
        HARawEvent(
            entity_id=f"device_tracker.fordpass_{VIN}_tracker",
            device_id=VIN,
            slug="tracker",
            state="not_home",
            payload={"state": "not_home", "attributes": {"gps_accuracy": 12}},
            recorded_at=STATE_TS,
            source_system="ha_fordpass",
        )
    )
    await db_session.flush()

    states = await op._fetch_states()

    assert [entity_id.rsplit("_", 1)[-1] for _, entity_id, _, _ in states] == [
        "events",
        "metrics",
        "elveh",
    ]
    timestamps = [ts for ts, _, _, _ in states]
    assert timestamps == sorted(timestamps)
    assert all(isinstance(payload, dict) for _, _, payload, _ in states)
    # Each row hands back the unit system it was captured under, not one
    # value shared across the batch.
    assert [config["unit_system"] for _, _, _, config in states] == [
        METRIC_UNIT_SYSTEM,
        METRIC_UNIT_SYSTEM,
        IMPERIAL_UNIT_SYSTEM,
    ]


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
async def test_offline_replay_reads_imperial_payloads_as_imperial(op, db_session):
    """No live connection, imperial install: values must not be read as metric.

    ha-fordpass localizes the elveh trip attributes into Home Assistant's
    unit system. Replaying them without it stores miles as kilometres and °F
    as °C, and the wrong distance stops the row matching the trip the events
    payload created — so a spurious second trip appears alongside it.
    """
    await VehicleFactory.create(db_session, device_id=VIN)
    await _seed_trip_entities(
        db_session,
        fixture="imperial_ha_imperial_vehicle",
        unit_system=IMPERIAL_UNIT_SYSTEM,
    )

    await op.execute(db_session)

    trips = await _trips(db_session)
    assert len(trips) == 1, "elveh was read in the wrong units and duplicated the trip"
    trip = trips[0]
    assert float(trip.distance) == pytest.approx(19.0, abs=0.05)
    assert float(trip.cabin_temp) == pytest.approx(20.0, abs=0.5)
    assert float(trip.ambient_temp) == pytest.approx(15.0, abs=0.5)


@pytest.mark.db
async def test_units_come_from_each_row_not_the_newest_in_the_table(op, db_session):
    """A later row captured under a different unit system must not retune the rest.

    Resolving the unit system once for the whole replay reads the imperial
    elveh payload as metric, and the wrong distance stops it matching the
    trip the events payload created — so a duplicate appears at 11.81 km.
    """
    await VehicleFactory.create(db_session, device_id=VIN)
    await _seed_trip_entities(
        db_session,
        fixture="imperial_ha_imperial_vehicle",
        unit_system=IMPERIAL_UNIT_SYSTEM,
    )
    # Not a trip entity, captured later under a different unit system: a
    # global "newest non-null" probe picks this one.
    await RawEventFactory.create(
        db_session,
        suffix="soc",
        device_id=VIN,
        recorded_at=STATE_TS + timedelta(days=1),
        ha_unit_system=METRIC_UNIT_SYSTEM,
    )

    await op.execute(db_session)

    trips = await _trips(db_session)
    assert len(trips) == 1, "elveh was read under another row's unit system"
    assert float(trips[0].distance) == pytest.approx(19.0, abs=0.05)
    assert float(trips[0].cabin_temp) == pytest.approx(20.0, abs=0.5)


@pytest.mark.db
async def test_states_needing_an_unknown_unit_system_are_skipped(op, db_session):
    """With no recorded unit system and no live runtime, do not assume metric."""
    await VehicleFactory.create(db_session, device_id=VIN)
    await _seed_trip_entities(
        db_session,
        fixture="imperial_ha_imperial_vehicle",
        unit_system=None,
    )

    await op.execute(db_session)

    # elveh and metrics skipped; the events payload is raw metric regardless.
    assert op.last_details["skipped_unknown_units"] == 2
    trips = await _trips(db_session)
    assert len(trips) == 1
    assert float(trips[0].distance) == pytest.approx(19.0, abs=0.05)


@pytest.mark.db
async def test_a_live_connection_supplies_no_units_for_rows_without_them(
    op, db_session, monkeypatch
):
    """The branch production actually takes: Home Assistant connected.

    Nothing stops a user running this with a live connection, and the live
    runtime's config is today's *global* unit system. Applying it to a row
    that recorded none is the same global resolution the per-row config
    exists to prevent — and reads an imperial payload as metric.
    """
    from web.services.ingestion.supervisor import supervisor

    class _LiveRuntime:
        source_name = "ha_fordpass"
        instance_label = "default"
        detected_vin = VIN
        health = {"connected": True}
        _ha_config = {"unit_system": METRIC_UNIT_SYSTEM}

    monkeypatch.setattr(supervisor, "_runtimes", {1: _LiveRuntime()})

    await VehicleFactory.create(db_session, device_id=VIN)
    # Every event of a session whose get_config handshake failed looks
    # like this: real payloads, no recorded unit system.
    await _seed_trip_entities(
        db_session,
        fixture="imperial_ha_imperial_vehicle",
        unit_system=None,
    )

    await op.execute(db_session)

    assert op.last_details["skipped_unknown_units"] == 2
    trips = await _trips(db_session)
    assert len(trips) == 1, "imperial rows were read under the live global units"
    assert float(trips[0].distance) == pytest.approx(19.0, abs=0.05)


@pytest.mark.db
async def test_apply_never_consults_the_live_supervisor(op, db_session, monkeypatch):
    """The invariant itself: a full replay must not reach for live state at all.

    Every unit-correctness assertion above depends on the consequence of
    touching the live runtime being visible in a trip row. This one does not:
    a supervisor call added anywhere in the cycle, for any reason, fails here
    even when it disturbs nothing else.
    """
    from web.services.ingestion.supervisor import supervisor

    consulted: list[tuple[tuple, dict]] = []
    real_get_runtime = supervisor.get_runtime

    def _recording(*args, **kwargs):
        consulted.append((args, kwargs))
        return real_get_runtime(*args, **kwargs)

    monkeypatch.setattr(supervisor, "get_runtime", _recording)

    await VehicleFactory.create(db_session, device_id=VIN)
    await _seed_trip_entities(
        db_session,
        fixture="imperial_ha_imperial_vehicle",
        unit_system=IMPERIAL_UNIT_SYSTEM,
    )

    await op.apply(db_session)

    assert not consulted, (
        f"ArchiveReplay.apply() consulted the live supervisor: {consulted}"
    )


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

    Runs on its own engine with committed archive rows: rollback_session
    opens a second connection, which deadlocks against the row locks the
    db_session fixture's open transaction holds. The committed rows are
    deleted in the finally block, which assumes a serial test suite — no
    other test may be reading ha_raw_events concurrently.
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
        preview = await op.preview(None)
        diffs = preview.diffs

        assert diffs
        assert {d.action for d in diffs} == {"insert"}
        # Recovered rows name the archived event they were rebuilt from.
        for group in preview.groups:
            assert group.context["replayed from"] == "LightningROD event archive"
            assert "sensor.fordpass_" in group.context["states applied"]
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


def test_tracker_is_not_a_trip_entity():
    """Replay must never dispatch device_tracker payloads at slug handlers."""
    assert "tracker" not in ArchiveReplay.TRIP_ENTITY_SUFFIXES


def test_registered_between_telemetry_derive_and_recorder_replay():
    from web.services.repair.registry import REPAIR_REGISTRY, get_operation

    slugs = [op.slug for op in REPAIR_REGISTRY]
    assert slugs.index("archive-replay") == slugs.index("telemetry-derive") + 1
    assert slugs.index("archive-replay") == slugs.index("recorder-replay") - 1
    assert get_operation("archive-replay").runs_when_clean is True
