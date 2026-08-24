"""RawEventArchive: scope, denormalization, timestamps, dedup, failure isolation.

The writer opens its own session in production; here ``AsyncSessionLocal`` is
swapped for the rollback-isolated test session so writes land inside the
fixture's transaction.
"""

from __future__ import annotations

import copy
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from db.models.raw_event import HARawEvent
from tests.conftest import FixedSessionFactory
from tests.factories.raw_events import RawEventFactory
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


@pytest.fixture
def archive(monkeypatch, db_session):
    """A fresh archive instance writing through the test session.

    Retention is parked so the write-path tests aren't perturbed by a prune;
    the retention tests re-arm it explicitly.
    """
    import web.services.ingestion.raw_archive as module

    monkeypatch.setattr(module, "AsyncSessionLocal", FixedSessionFactory(db_session))
    instance = RawEventArchive()
    instance._prune_due_at = time.monotonic() + module.PRUNE_INTERVAL
    return instance


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
async def test_recorded_at_prefers_last_updated(archive, db_session):
    """last_updated wins: it is the only field that moves on every update."""
    fresher = EVENT_TS + timedelta(hours=5)
    state = _state("soc")
    state["last_updated"] = fresher.isoformat()

    await archive.store(_entity("soc"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].recorded_at.replace(tzinfo=None) == fresher.replace(tzinfo=None)


@pytest.mark.db
async def test_recorded_at_falls_back_to_last_changed(archive, db_session):
    """A missing last_updated falls through to last_changed."""
    state = _state("soc")
    del state["last_updated"]

    await archive.store(_entity("soc"), state, config_id=1)

    rows = await _rows(db_session)
    assert rows[0].recorded_at.replace(tzinfo=None) == EVENT_TS.replace(tzinfo=None)


@pytest.mark.db
async def test_recorded_at_falls_back_to_now(archive, db_session):
    """With no usable timestamp the arrival time is recorded instead."""
    before = datetime.now(UTC)
    state = _state("soc")
    del state["last_updated"]
    del state["last_changed"]

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
async def test_attribute_only_updates_are_all_kept(archive, db_session):
    """The events entity's state string barely moves while its payload churns.

    Home Assistant leaves last_changed frozen through those updates, so
    keying the dedup index off it would throw away every event after the
    first — exactly the payloads this archive exists to keep.
    """
    first = _state("events")
    second = _state("events")
    second["last_updated"] = (EVENT_TS + timedelta(minutes=29)).isoformat()
    second["last_changed"] = first["last_changed"]  # state string unchanged
    second["attributes"]["customEvents"] = {"second-trip": {"trip": 2}}

    await archive.store(_entity("events"), first, config_id=1)
    await archive.store(_entity("events"), second, config_id=1)

    rows = await _rows(db_session)
    assert len(rows) == 2
    assert [row.payload["attributes"]["customEvents"] for row in rows] == [
        first["attributes"]["customEvents"],
        second["attributes"]["customEvents"],
    ]


@pytest.mark.db
async def test_store_swallows_database_errors(archive, db_session, monkeypatch):
    """A write failure is logged, never raised — ingestion must keep running."""
    import web.services.ingestion.raw_archive as module

    def _no_connection():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(module, "AsyncSessionLocal", _no_connection)

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert await _count(db_session) == 0


# ---------------------------------------------------------------------------
# Retention prune
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_prune_drops_expired_rows_and_keeps_recent_ones(archive, db_session):
    """A store past the throttle deletes what fell out of the retention window."""
    archive._prune_due_at = 0.0
    now = datetime.now(UTC)
    old = await RawEventFactory.create(
        db_session, suffix="elveh", recorded_at=now - timedelta(days=120)
    )
    recent = await RawEventFactory.create(
        db_session, suffix="metrics", recorded_at=now - timedelta(days=2)
    )

    await archive.store(_entity("soc"), _state("soc", now), config_id=1)

    remaining = {row.id for row in await _rows(db_session)}
    assert old.id not in remaining
    assert recent.id in remaining


@pytest.mark.db
async def test_zero_retention_deletes_nothing(archive, db_session):
    """0 days means keep forever."""
    archive._prune_due_at = 0.0
    await set_app_setting(db_session, "raw_archive_retention_days", "0")
    old = await RawEventFactory.create(
        db_session, suffix="elveh", recorded_at=datetime.now(UTC) - timedelta(days=900)
    )

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert old.id in {row.id for row in await _rows(db_session)}


@pytest.mark.db
async def test_second_store_inside_the_throttle_does_not_prune(archive, db_session):
    """One retention pass per tick — the next event must not re-run the delete."""
    archive._prune_due_at = 0.0
    now = datetime.now(UTC)
    await archive.store(_entity("soc"), _state("soc", now), config_id=1)

    stale = await RawEventFactory.create(
        db_session, suffix="elveh", recorded_at=now - timedelta(days=120)
    )
    await archive.store(_entity("metrics"), _state("metrics", now), config_id=1)

    assert stale.id in {row.id for row in await _rows(db_session)}


@pytest.mark.db
async def test_full_batch_shortens_the_next_throttle(archive, db_session, monkeypatch):
    """A backlog drains over minutes instead of waiting a full day."""
    import web.services.ingestion.raw_archive as module

    monkeypatch.setattr(module, "PRUNE_BATCH", 2)
    archive._prune_due_at = 0.0
    now = datetime.now(UTC)
    for suffix in ("elveh", "metrics", "soc"):
        await RawEventFactory.create(
            db_session, suffix=suffix, recorded_at=now - timedelta(days=120)
        )

    await archive.store(_entity("events"), _state("events", now), config_id=1)

    assert len(await _rows(db_session)) == 2  # one archived, one leftover expired
    assert archive._prune_due_at - time.monotonic() <= module.PRUNE_BACKLOG_INTERVAL


@pytest.mark.db
async def test_retention_still_runs_while_the_archive_is_disabled(archive, db_session):
    """Switching the archive off must still let the stored rows age out."""
    await set_app_setting(db_session, "raw_archive_enabled", "false")
    archive._prune_due_at = 0.0
    old = await RawEventFactory.create(
        db_session, suffix="elveh", recorded_at=datetime.now(UTC) - timedelta(days=120)
    )

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert await _count(db_session) == 0  # nothing new archived, old row pruned
    assert old.id not in {row.id for row in await _rows(db_session)}


@pytest.mark.db
async def test_failed_prune_still_re_arms_the_throttle(archive, db_session):
    """A prune that raises must not retry on every single event afterwards."""
    archive._prune_due_at = 0.0

    async def _boom(_retention_days):
        raise OverflowError("date value out of range")

    archive._prune_expired = _boom

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert await _count(db_session) == 1  # the write itself still landed
    assert archive._prune_due_at > time.monotonic()


@pytest.mark.db
async def test_partial_batch_keeps_the_long_throttle(archive, db_session, monkeypatch):
    """Nothing left to drain means the next pass is a day away."""
    import web.services.ingestion.raw_archive as module

    monkeypatch.setattr(module, "PRUNE_BATCH", 10)
    archive._prune_due_at = 0.0
    await RawEventFactory.create(
        db_session, suffix="elveh", recorded_at=datetime.now(UTC) - timedelta(days=120)
    )

    await archive.store(_entity("soc"), _state("soc"), config_id=1)

    assert (
        archive._prune_due_at - time.monotonic() > module.PRUNE_BACKLOG_INTERVAL
    )
