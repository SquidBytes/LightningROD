"""RawEventArchive: scope, denormalization, timestamps, dedup, failure isolation.

The writer opens its own session in production; here ``AsyncSessionLocal`` is
swapped for the rollback-isolated test session so writes land inside the
fixture's transaction.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from db.models.raw_event import HARawEvent
from web.queries.settings import set_app_setting
from web.services.ingestion.raw_archive import RawEventArchive

pytestmark = pytest.mark.unit

VIN = "TESTVIN001"
EVENT_TS = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)

_FIXTURE = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "ha_payloads"
        / "metric_ha_metric_vehicle.json"
    ).read_text()
)


def _entity(suffix: str) -> str:
    return f"sensor.fordpass_{VIN}_{suffix}"


def _state(suffix: str, ts: datetime = EVENT_TS) -> dict:
    """Fixture state for one entity, re-VINed and restamped to `ts`."""
    state = copy.deepcopy(_FIXTURE[f"sensor.fordpass_YOUR_VIN_{suffix}"])
    state["entity_id"] = _entity(suffix)
    state["last_changed"] = ts.isoformat()
    state["last_updated"] = ts.isoformat()
    return state


class _SessionFactory:
    """Hands the writer the test session instead of a fresh production one."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_):
        return False


@pytest.fixture
def archive(monkeypatch, db_session):
    """A fresh archive instance writing through the test session."""
    import web.services.ingestion.raw_archive as module

    monkeypatch.setattr(module, "AsyncSessionLocal", _SessionFactory(db_session))
    return RawEventArchive()


async def _count(db) -> int:
    return await db.scalar(select(func.count()).select_from(HARawEvent))


async def _rows(db) -> list[HARawEvent]:
    result = await db.execute(select(HARawEvent).order_by(HARawEvent.id))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_store_writes_one_row_with_denormalized_scalars(archive, db_session):
    """One event becomes one row; the filterable columns come out of the payload."""
    state = _state("events")

    await archive.store(_entity("events"), state, config_id=3)

    rows = await _rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_id == _entity("events")
    assert row.device_id == VIN
    assert row.slug == "events"
    assert row.state == "ok"
    assert row.config_id == 3
    assert row.source_system == "ha_fordpass"
    assert row.ingest_schema_version is not None
    assert row.ingested_at is not None
    assert row.recorded_at.replace(tzinfo=None) == EVENT_TS.replace(tzinfo=None)


@pytest.mark.db
async def test_payload_roundtrips_dict_equal(archive, db_session):
    """The stored payload comes back identical, nested dicts and lists included."""
    state = _state("events")

    await archive.store(_entity("events"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].payload == state
    trip = rows[0].payload["attributes"]["customEvents"][
        "xev-key-off-trip-segment-data"
    ]["oemData"]["trip_data"]["stringArrayValue"]
    assert isinstance(trip, list)


@pytest.mark.db
async def test_every_fixture_entity_is_archived(archive, db_session):
    """All seven fixture entities are fordpass-scoped and each writes a row."""
    for key in _FIXTURE:
        suffix = key.rsplit("_", 1)[-1]
        await archive.store(_entity(suffix), _state(suffix), config_id=1)

    assert await _count(db_session) == len(_FIXTURE) == 7


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_recorded_at_prefers_last_changed(archive, db_session):
    """last_changed wins over last_updated."""
    state = _state("soc")
    state["last_updated"] = (EVENT_TS + timedelta(hours=5)).isoformat()

    await archive.store(_entity("soc"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].recorded_at.replace(tzinfo=None) == EVENT_TS.replace(tzinfo=None)


@pytest.mark.db
async def test_recorded_at_falls_back_to_last_updated(archive, db_session):
    """A missing last_changed falls through to last_updated."""
    fallback = EVENT_TS + timedelta(hours=2)
    state = _state("soc")
    del state["last_changed"]
    state["last_updated"] = fallback.isoformat()

    await archive.store(_entity("soc"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].recorded_at.replace(tzinfo=None) == fallback.replace(tzinfo=None)


@pytest.mark.db
async def test_recorded_at_falls_back_to_now(archive, db_session):
    """With no usable timestamp the arrival time is recorded instead."""
    before = datetime.now(UTC)
    state = _state("soc")
    del state["last_changed"]
    del state["last_updated"]

    await archive.store(_entity("soc"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].recorded_at.replace(tzinfo=None) >= before.replace(tzinfo=None)


@pytest.mark.db
async def test_offset_timestamps_are_normalized_to_utc(archive, db_session):
    """A non-UTC offset resolves to UTC before binding — SQLite drops tzinfo."""
    state = _state("soc")
    state["last_changed"] = "2026-04-19T14:00:00+02:00"
    state["last_updated"] = state["last_changed"]

    await archive.store(_entity("soc"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].recorded_at.replace(tzinfo=None) == EVENT_TS.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Scope + enable switch
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_non_fordpass_entity_writes_nothing(archive, db_session):
    """Every other entity in the Home Assistant instance is out of scope."""
    await archive.store(
        "sensor.living_room_motion", {"state": "on"}, config_id=1
    )
    await archive.store("sensor.gas_price_station", {"state": "3.50"}, config_id=1)

    assert await _count(db_session) == 0


@pytest.mark.db
async def test_empty_state_writes_nothing(archive, db_session):
    """A state_changed with no new_state (entity removed) is skipped."""
    await archive.store(_entity("soc"), {}, config_id=1)

    assert await _count(db_session) == 0


@pytest.mark.db
async def test_disabled_archive_writes_nothing(archive, db_session):
    """Switching the archive off stops new rows on the next settings read."""
    await set_app_setting(db_session, "raw_archive_enabled", "false")

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert await _count(db_session) == 0


@pytest.mark.db
async def test_settings_cache_is_invalidated_on_demand(archive, db_session):
    """A settings save takes effect without waiting out the cache TTL."""
    await archive.store(_entity("soc"), _state("soc"), config_id=1)
    assert await _count(db_session) == 1

    await set_app_setting(db_session, "raw_archive_enabled", "false")
    archive.invalidate_settings()
    await archive.store(_entity("elveh"), _state("elveh"), config_id=1)

    assert await _count(db_session) == 1


# ---------------------------------------------------------------------------
# Dedup + failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_same_entity_and_timestamp_stores_once(archive, db_session):
    """A reconnect snapshot re-emits states verbatim; the second insert no-ops."""
    await archive.store(_entity("events"), _state("events"), config_id=1)
    await archive.store(_entity("events"), _state("events"), config_id=1)

    assert await _count(db_session) == 1


@pytest.mark.db
async def test_store_swallows_database_errors(archive, db_session, monkeypatch):
    """A write failure is logged, never raised — ingestion must keep running."""
    import web.services.ingestion.raw_archive as module

    def _no_connection():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(module, "AsyncSessionLocal", _no_connection)

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert await _count(db_session) == 0
