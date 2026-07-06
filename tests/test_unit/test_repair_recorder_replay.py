"""RecorderReplay: merge order, enrichment, D-05 revert, dry-run, disconnected.

Covers WP3 of the data-repair capability:
  - _fetch_states merges multi-entity history chronologically
  - execute fills NULL trip fields from replayed states and is idempotent
  - non-mutable (csv_import) rows touched by replay are reverted byte-identical
  - preview runs the full replay (including the elveh handler-commit path)
    inside rollback_session and persists nothing
  - disconnected/absent runtime -> window None, census 0
  - _fetch_entity_history forwards the new end_time query param
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair.recorder_replay import RecorderReplay
from web.services.repair.snapshot import serialize_row

pytestmark = pytest.mark.unit

# Underscore-free like a real VIN: extract_slug/get_device_id split the
# entity_id on "_", so the fixture's YOUR_VIN placeholder can't be used here.
VIN = "TESTVIN001"
STATE_TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

_FIXTURE = json.loads(
    (
        Path(__file__).parent.parent / "fixtures" / "ha_payloads" / "metric_ha_metric_vehicle.json"
    ).read_text()
)


def _entity(suffix: str) -> str:
    return f"sensor.fordpass_{VIN}_{suffix}"


def _state(suffix: str, ts: datetime, **attr_overrides) -> dict:
    """Fixture state for a trip entity, restamped to `ts` and re-VINed."""
    state = copy.deepcopy(_FIXTURE[f"sensor.fordpass_YOUR_VIN_{suffix}"])
    state["entity_id"] = _entity(suffix)
    state["last_changed"] = ts.isoformat()
    state["last_updated"] = ts.isoformat()
    state["attributes"].update(attr_overrides)
    return state


class FakeRuntime:
    """Minimal HAWebSocketRuntime stand-in serving canned per-entity history."""

    def __init__(self, histories: dict[str, list[dict]], connected: bool = True):
        self._ha_config = {"unit_system": {"length": "km", "temperature": "°C"}}
        self.config_id = 1
        self.detected_vin = VIN
        self.health = {"connected": connected}
        self._histories = histories
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def _fetch_entity_history(
        self, entity_id, start_time_iso=None, end_time_iso=None
    ):
        self.calls.append((entity_id, start_time_iso, end_time_iso))
        start = datetime.fromisoformat(start_time_iso) if start_time_iso else None
        end = datetime.fromisoformat(end_time_iso) if end_time_iso else None
        out = []
        for state in self._histories.get(entity_id, []):
            ts = datetime.fromisoformat(state["last_updated"])
            if (start is None or ts >= start) and (end is None or ts < end):
                out.append(state)
        return out


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


# ---------------------------------------------------------------------------
# Chronological merge
# ---------------------------------------------------------------------------


async def test_fetch_states_merges_chronologically():
    """States from multiple entities come back ascending by state timestamp."""
    histories = {
        _entity("events"): [
            _state("events", STATE_TS),
            _state("events", STATE_TS + timedelta(hours=4)),
        ],
        _entity("elveh"): [_state("elveh", STATE_TS + timedelta(hours=2))],
        _entity("metrics"): [
            _state("metrics", STATE_TS + timedelta(hours=3)),
            _state("metrics", STATE_TS + timedelta(hours=1)),
        ],
    }
    op = RecorderReplay(runtime=FakeRuntime(histories))

    states = await op._fetch_states()

    assert len(states) == 5
    timestamps = [ts for ts, _, _ in states]
    assert timestamps == sorted(timestamps)
    suffixes = [entity_id.rsplit("_", 1)[-1] for _, entity_id, _ in states]
    assert suffixes == ["events", "metrics", "elveh", "metrics", "events"]


# ---------------------------------------------------------------------------
# Enrichment + idempotency
# ---------------------------------------------------------------------------


def _full_histories() -> dict[str, list[dict]]:
    return {
        _entity(suffix): [_state(suffix, STATE_TS)]
        for suffix in RecorderReplay.TRIP_ENTITY_SUFFIXES
    }


@pytest.mark.db
async def test_execute_fills_null_fields_and_is_idempotent(db_session):
    """Replay enriches a NULL-field trip; a second run changes nothing."""
    await VehicleFactory.create(db_session, device_id=VIN)
    # Fixture events payload carries no updateTime, so the deterministic-id
    # path is unavailable; seed distance/energy/end_time so the legacy
    # predicate match (±0.01, ±24 h) hits instead.
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

    from db.models.battery_status import EVBatteryStatus
    from db.models.vehicle_status import EVVehicleStatus

    async def _status_counts() -> tuple[int, int]:
        battery = await db_session.scalar(
            select(func.count()).select_from(EVBatteryStatus)
        )
        vehicle = await db_session.scalar(
            select(func.count()).select_from(EVVehicleStatus)
        )
        return battery, vehicle

    status_before = await _status_counts()

    op = RecorderReplay(runtime=FakeRuntime(_full_histories()))
    assert await op.census(db_session) == 1

    changed = await op.execute(db_session)

    assert changed == 1
    details = op.last_details
    assert details["states_replayed"] == 3
    assert details["errors"] == 0
    assert details["trips_recovered"] == 0
    assert details["filled"]["duration"] == 1
    assert details["filled"]["ambient_temp"] == 1
    assert float(trip.duration) == pytest.approx(1800.0)
    assert float(trip.ambient_temp) == pytest.approx(15.0)
    assert float(trip.cabin_temp) == pytest.approx(20.0)
    assert float(trip.outside_air_temp) == pytest.approx(15.0)
    assert float(trip.range_regenerated) == pytest.approx(2.1)
    assert float(trip.driving_score) == pytest.approx(85.0)
    assert float(trip.speed_score) == pytest.approx(60.0)
    assert trip.start_time == STATE_TS - timedelta(seconds=1800)

    # Trips-only scope: replay never writes battery/vehicle status rows.
    assert await _status_counts() == status_before

    # Second run converges: same states, zero new fills or changes.
    second = await op.execute(db_session)
    assert second == 0
    assert op.last_details["filled"] == {}
    assert op.last_details["trips_recovered"] == 0


# ---------------------------------------------------------------------------
# D-05: protected rows revert
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_csv_import_row_stays_byte_identical(db_session):
    """A csv_import trip matched by replay dedup is reverted untouched."""
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

    op = RecorderReplay(runtime=FakeRuntime(_full_histories()))
    assert await op.census(db_session) == 0  # invisible to the census

    changed = await op.execute(db_session)

    assert changed == 0
    assert op.last_details["protected_reverted"] >= 1
    assert serialize_row(trip) == before
    # Dedup matched the protected row, so no ha_fordpass twin was inserted.
    count = await db_session.scalar(
        select(func.count())
        .select_from(EVTripMetrics)
        .where(EVTripMetrics.device_id == VIN)
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Census-0 apply: recover never-ingested trips
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_apply_recovers_trips_at_census_zero(db_session):
    """apply() replays even with nothing to snapshot, recovering missing trips."""
    op = RecorderReplay(runtime=FakeRuntime(_full_histories()))
    assert await op.census(db_session) == 0  # no trips exist at all

    result = await op.apply(db_session)

    assert result.run_id is None
    assert result.snapshot_rows == 0
    assert result.affected >= 1
    assert result.details["trips_recovered"] >= 1
    count = await db_session.scalar(
        select(func.count())
        .select_from(EVTripMetrics)
        .where(EVTripMetrics.device_id == VIN)
    )
    assert count == 1


# ---------------------------------------------------------------------------
# Dry-run preview
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_preview_returns_diffs_but_persists_nothing():
    """Preview replays (through the elveh commit path) yet leaves the DB unchanged."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import TEST_DB_URL, _attach_sqlite_pragmas

    histories = {
        _entity("events"): [_state("events", STATE_TS)],
        # Distinct distance/energy so the elveh state can't match the
        # events-created trip and takes the trip-creation branch, which
        # calls db.commit() inside the handler.
        _entity("elveh"): [
            _state(
                "elveh",
                STATE_TS + timedelta(hours=1),
                tripDistanceTraveled=30,
                tripEnergyConsumed=10.0,
            )
        ],
    }
    op = RecorderReplay(runtime=FakeRuntime(histories))
    engine = create_async_engine(TEST_DB_URL)
    _attach_sqlite_pragmas(engine)
    op._rollback_engine = engine
    try:
        diffs = await op.preview(None)

        assert len(diffs) == 2
        assert {d.action for d in diffs} == {"insert"}
        assert all(d.after["device_id"] == VIN for d in diffs)

        async with engine.connect() as conn:
            persisted = await conn.scalar(
                select(func.count())
                .select_from(EVTripMetrics)
                .where(EVTripMetrics.device_id == VIN)
            )
        assert persisted == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Disconnected / missing runtime
# ---------------------------------------------------------------------------


async def test_disconnected_runtime_census_zero():
    op = RecorderReplay(runtime=FakeRuntime(_full_histories(), connected=False))
    assert await op.recorder_window() is None
    assert await op.census(None) == 0
    assert await op._fetch_states() == []


async def test_missing_runtime_census_zero(monkeypatch):
    from web.services.ingestion.supervisor import supervisor

    monkeypatch.setattr(supervisor, "_runtimes", {})
    op = RecorderReplay()  # falls back to the supervisor lookup -> None
    assert await op.recorder_window() is None
    assert await op.census(None) == 0
    assert await op.affected_rows(None) == []


# ---------------------------------------------------------------------------
# Runtime end_time forwarding
# ---------------------------------------------------------------------------


async def test_fetch_entity_history_forwards_end_time(monkeypatch):
    """end_time_iso becomes the end_time query param; omitted when None."""
    import httpx

    from web.services.ingestion.ha_websocket import HAWebSocketRuntime

    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [[{"state": "ok"}]]

    async def _fake_get(self, url, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    runtime = HAWebSocketRuntime(1, "http://ha.test:8123", "token")

    states = await runtime._fetch_entity_history(
        "sensor.test",
        "2026-04-01T00:00:00+00:00",
        end_time_iso="2026-04-02T00:00:00+00:00",
    )
    assert states == [{"state": "ok"}]
    assert captured["url"].endswith("/api/history/period/2026-04-01T00:00:00+00:00")
    assert captured["params"]["end_time"] == "2026-04-02T00:00:00+00:00"

    await runtime._fetch_entity_history("sensor.test")
    assert "end_time" not in captured["params"]
