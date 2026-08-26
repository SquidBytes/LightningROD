"""The connect-time catch-up sweep: what it replays and what that costs.

A parked car can sit for hours without moving, so the sweep that runs on
every connect is the only thing recording vehicle position after a restart.
These tests drive the real handshake against the simulator and read the
rows it produced.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from db.models.location import EVLocation
from db.models.raw_event import HARawEvent
from db.models.vehicle import EVVehicle
from tests.conftest import FixedSessionFactory
from tests.test_ha_sim.simulator import HASimulator

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

VIN = "SWEEPVIN001"
TOKEN = "test-token-valid"
TRACKER = f"device_tracker.fordpass_{VIN}_tracker"
ODOMETER = f"sensor.fordpass_{VIN}_odometer"
# A car parked since this morning: the states are hours old but still current.
PARKED_AT = datetime.now(UTC) - timedelta(hours=6)


def _tracker_state(ts: datetime = PARKED_AT, lat: float = 42.123456) -> dict:
    """A device_tracker state as the FordPass integration emits it."""
    return {
        "entity_id": TRACKER,
        "state": "not_home",
        "attributes": {
            "latitude": lat,
            "longitude": -71.654321,
            "gps_accuracy": 12,
            "Altitude": 47.5,
            "in_zones": [],
            "gpsCoordinateMethod": "GPS",
            "gpsDimension": "3D",
        },
        "last_changed": ts.isoformat(),
        "last_updated": ts.isoformat(),
    }


def _odometer_state(ts: datetime = PARKED_AT) -> dict:
    return {
        "entity_id": ODOMETER,
        "state": "42123.0",
        "attributes": {"unit_of_measurement": "km"},
        "last_changed": ts.isoformat(),
        "last_updated": ts.isoformat(),
    }


@pytest.fixture
def catchup_db(monkeypatch, db_session):
    """Route every session the connect path opens at the test session."""
    import db.engine as db_engine
    import web.services.ingestion.raw_archive as archive_module
    from web.services.ingestion.raw_archive import raw_archive

    factory = FixedSessionFactory(db_session)
    monkeypatch.setattr(db_engine, "AsyncSessionLocal", factory)
    monkeypatch.setattr(archive_module, "AsyncSessionLocal", factory)
    raw_archive.reset()
    # Retention is parked so a prune cannot delete the rows under test.
    raw_archive._prune_due_at = time.monotonic() + archive_module.PRUNE_INTERVAL
    yield db_session
    raw_archive.reset()


async def _connect_once(sim: HASimulator, states: list[dict]):
    """Run one full handshake, including the catch-up sweep, then hang up."""
    from web.services.ingestion.ha_websocket import HAWebSocketRuntime

    sim.set_entity_states(states)
    runtime = HAWebSocketRuntime(1, sim.ws_url, TOKEN)
    try:
        await runtime._connect_and_subscribe(sim.ws_url, TOKEN)
    finally:
        await runtime._close_ws()
    return runtime


async def _archived(db, entity_id: str) -> list[HARawEvent]:
    result = await db.execute(
        select(HARawEvent)
        .where(HARawEvent.entity_id == entity_id)
        .order_by(HARawEvent.recorded_at)
    )
    return list(result.scalars().all())


async def test_catchup_sweep_archives_the_device_tracker(ha_simulator, catchup_db):
    """Position is recorded at connect, not only when the car next moves."""
    await _connect_once(ha_simulator, [_odometer_state(), _tracker_state()])
    await catchup_db.flush()

    rows = await _archived(catchup_db, TRACKER)
    assert len(rows) == 1, "the catch-up sweep skipped the device tracker"
    row = rows[0]
    assert row.device_id == VIN
    assert row.slug == "tracker"
    assert row.payload["attributes"]["gps_accuracy"] == 12
    assert row.payload["attributes"]["Altitude"] == 47.5
    # The sweep replays the entity's own timestamp, so the row dates the
    # reading rather than the reconnect — which is also what lets the dedup
    # index recognise a re-send.
    drift = abs((row.recorded_at.replace(tzinfo=UTC) - PARKED_AT).total_seconds())
    assert drift < 1, f"row stamped {row.recorded_at}, reading was {PARKED_AT}"
    # The sensor half of the sweep still works.
    assert len(await _archived(catchup_db, ODOMETER)) == 1


async def test_reconnect_replays_the_tracker_without_duplicating_it(
    ha_simulator, catchup_db
):
    """A re-sent reading is dropped; a genuinely new one is not.

    Both sides of the dedup condition, because a blocker that swallowed the
    second, distinct reading would look identical from the first half alone.
    """
    states = [_tracker_state()]
    await _connect_once(ha_simulator, states)
    await _connect_once(ha_simulator, states)
    await catchup_db.flush()

    assert len(await _archived(catchup_db, TRACKER)) == 1, (
        "the reconnect sweep wrote a second row for the same reading"
    )

    # The car moved, then we reconnected again.
    moved_at = PARKED_AT + timedelta(minutes=30)
    await _connect_once(ha_simulator, [_tracker_state(moved_at, lat=42.9)])
    await catchup_db.flush()

    rows = await _archived(catchup_db, TRACKER)
    assert len(rows) == 2, "dedup discarded a distinct tracker reading"
    assert [r.payload["attributes"]["latitude"] for r in rows] == [42.123456, 42.9]


async def test_catchup_sweep_routes_no_slug_handler_at_the_tracker(
    ha_simulator, catchup_db
):
    """Widening the sweep must not feed tracker payloads to sensor handlers."""
    from web.services.sources.ha_fordpass.dispatch import dispatch_slug
    from web.services.sources.ha_fordpass.handlers import extract_slug

    assert extract_slug(TRACKER) is None

    await _connect_once(ha_simulator, [_tracker_state()])
    await catchup_db.flush()

    # dispatch_slug returned before `_ensure_vehicle_exists`, so the tracker
    # neither auto-created a vehicle nor wrote the gps handler's location row.
    assert await catchup_db.scalar(
        select(func.count()).select_from(EVVehicle).where(EVVehicle.device_id == VIN)
    ) == 0
    assert await catchup_db.scalar(
        select(func.count()).select_from(EVLocation).where(EVLocation.device_id == VIN)
    ) == 0

    # Directly, too: a tracker event handed to the dispatcher is a no-op.
    await dispatch_slug(TRACKER, _tracker_state(), {}, catchup_db, config_id=1)
    await catchup_db.flush()
    assert await catchup_db.scalar(
        select(func.count()).select_from(EVVehicle).where(EVVehicle.device_id == VIN)
    ) == 0
