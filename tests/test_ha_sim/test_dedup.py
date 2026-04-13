"""Integration tests for HA-side session and trip deduplication (Phase 23 backfill).

Covers:
- Charging session dedup via energytransferlogentry events (repeat, updated
  end_time, updated energy, fuzzy window).
- Cross-source dedup: manual/CSV session already exists, HA delivers matching
  event -> new row is flagged rather than silently duplicated.
- Trip dedup: regression coverage for the "3 trips for 1 real trip" bug noted
  in WORKING.md (line 132). Identical trip events should produce one row.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicles import VehicleFactory
from tests.test_ha_sim.simulator import (
    make_charging_session_event,
    make_trip_event,
)
from web.services.hass_processor import SENSOR_HANDLERS, extract_slug

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]


_TEST_DEVICE_ID = "TESTVIN001"

_HA_CONFIG = {
    "location_name": "Test Home",
    "time_zone": "America/New_York",
    "unit_system": {
        "length": "mi",
        "mass": "lb",
        "temperature": "\u00b0F",
        "volume": "gal",
    },
    "_fordpass_distance_unit": "mi",
    "_fordpass_temp_unit": "degF",
}


async def _dispatch_event(entity_id: str, new_state: dict, db) -> None:
    """Invoke the registered processor handler for a simulated HA event."""
    slug = extract_slug(entity_id)
    assert slug is not None, f"Could not extract slug from {entity_id}"
    handler = SENSOR_HANDLERS.get(slug)
    assert handler is not None, f"No handler registered for slug: {slug}"
    parts = entity_id[len("sensor.fordpass_"):].split("_", 1)
    device_id = parts[0]
    await handler(slug, new_state, _HA_CONFIG, device_id, db)


def _freeze_event_time(new_state: dict, when: datetime) -> None:
    """Rewrite the event + begin/end timestamps to a fixed moment.

    The charging_session helper uses _now_iso() for begin/end; tests need
    deterministic start_time windows to validate the ±30 minute dedup window.
    """
    iso = when.isoformat()
    new_state["last_changed"] = iso
    new_state["last_updated"] = iso
    attrs = new_state["attributes"]
    if "energyTransferDuration" in attrs:
        attrs["energyTransferDuration"]["begin"] = iso
        attrs["energyTransferDuration"]["end"] = iso
    attrs["timeStamp"] = iso


# ---------------------------------------------------------------------------
# Charging session dedup (HA)
# ---------------------------------------------------------------------------


async def test_ha_session_identical_repeat_is_deduped(db_session):
    """Two identical energytransferlogentry events -> only ONE session row."""
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    start = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    entity_id, state1 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=32.5,
        charge_type="DC_FAST",
    )
    _freeze_event_time(state1, start)
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    # Repeat identical event
    _, state2 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID,
        energy_kwh=32.5,
        charge_type="DC_FAST",
    )
    _freeze_event_time(state2, start)
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    result = await db_session.execute(
        select(EVChargingSession).where(
            EVChargingSession.device_id == _TEST_DEVICE_ID
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1, f"Expected 1 session after dup event, got {len(rows)}"


async def test_ha_session_near_duplicate_within_fuzzy_window(db_session):
    """Event arriving within ±30 min with ±10% energy should be deduped."""
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    entity_id, state1 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=40.0
    )
    _freeze_event_time(state1, base)
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    # Second event: 20 min later, energy within 10%
    _, state2 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=41.5
    )
    _freeze_event_time(state2, base + timedelta(minutes=20))
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVChargingSession).where(
            EVChargingSession.device_id == _TEST_DEVICE_ID
        )
    )).scalars().all()
    assert len(rows) == 1, f"Expected 1 session (fuzzy dedup), got {len(rows)}"


async def test_ha_session_outside_fuzzy_window_creates_new(db_session):
    """Event > 30 min later must NOT be deduped — real second session."""
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    entity_id, state1 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=40.0
    )
    _freeze_event_time(state1, base)
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    _, state2 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=40.0
    )
    _freeze_event_time(state2, base + timedelta(hours=3))
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVChargingSession).where(
            EVChargingSession.device_id == _TEST_DEVICE_ID
        )
    )).scalars().all()
    assert len(rows) == 2


async def test_ha_session_energy_diff_outside_tolerance_creates_new(db_session):
    """Within time window but energy > 10% apart should NOT be deduped."""
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    entity_id, state1 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=10.0
    )
    _freeze_event_time(state1, base)
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    _, state2 = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=25.0  # 150% apart
    )
    _freeze_event_time(state2, base + timedelta(minutes=5))
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVChargingSession).where(
            EVChargingSession.device_id == _TEST_DEVICE_ID
        )
    )).scalars().all()
    assert len(rows) == 2


async def test_ha_session_cross_source_flags_duplicate(db_session):
    """Manual/CSV session exists -> matching HA session is created but flagged.

    Per handle_energy_transfer: same source duplicates silently skip, but
    cross-source matches insert a second row with duplicate_of_id + needs_review.
    """
    from db.models.charging_session import EVChargingSession

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    # Pre-existing manual session
    existing = await ChargingSessionFactory.create(
        db_session,
        device_id=_TEST_DEVICE_ID,
        session_start_utc=base,
        energy_kwh=30.0,
        source_system="manual",
    )

    entity_id, state = make_charging_session_event(
        device_id=_TEST_DEVICE_ID, energy_kwh=30.5  # within 10%
    )
    _freeze_event_time(state, base + timedelta(minutes=10))
    await _dispatch_event(entity_id, state, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVChargingSession)
        .where(EVChargingSession.device_id == _TEST_DEVICE_ID)
        .order_by(EVChargingSession.id)
    )).scalars().all()

    assert len(rows) == 2, "Cross-source match should insert flagged duplicate"
    ha_row = [r for r in rows if r.source_system == "home_assistant"][0]
    assert ha_row.duplicate_of_id == existing.id
    assert ha_row.needs_review is True
    assert ha_row.review_type == "duplicate"


# ---------------------------------------------------------------------------
# Trip dedup
# ---------------------------------------------------------------------------


async def test_trip_identical_events_dedupe(db_session):
    """Two identical elveh trip events -> only ONE EVTripMetrics row.

    Regression coverage for WORKING.md line 132: "3 entries from today when
    I have only taken 1 trip". If this fails, the bug still exists.
    """
    from db.models.trip_metrics import EVTripMetrics

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, state1 = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=22.5,
        duration_minutes=35.0,
        efficiency=3.1,
        energy_consumed=7.2,
    )
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    # Clear the module-level _last_trip_values cache to simulate a second
    # delivery that is NOT suppressed by in-memory state (forcing the DB-level
    # dedup check to be the one under test).
    from web.services import hass_processor
    hass_processor._last_trip_values.clear()

    _, state2 = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=22.5,
        duration_minutes=35.0,
        efficiency=3.1,
        energy_consumed=7.2,
    )
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVTripMetrics).where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
    )).scalars().all()
    assert len(rows) == 1, (
        f"Trip dedup failed: expected 1 row, got {len(rows)}. "
        "Regression of WORKING.md#132 duplicate-trip bug."
    )


async def test_trip_in_memory_cache_suppresses_repeat(db_session):
    """Two identical events in the SAME process (cache intact) -> 1 row.

    This exercises the `_last_trip_values` fast-path guard. Separate from the
    DB-level dedup test above (which clears the cache).
    """
    from db.models.trip_metrics import EVTripMetrics

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, state1 = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=12.0,
        duration_minutes=20.0,
        efficiency=3.0,
        energy_consumed=4.0,
    )
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    _, state2 = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=12.0,
        duration_minutes=20.0,
        efficiency=3.0,
        energy_consumed=4.0,
    )
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVTripMetrics).where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
    )).scalars().all()
    assert len(rows) == 1


async def test_trip_different_values_create_separate_rows(db_session):
    """Genuinely different trips (new distance/duration) -> new row each."""
    from db.models.trip_metrics import EVTripMetrics

    await VehicleFactory.create(db_session, device_id=_TEST_DEVICE_ID)

    entity_id, state1 = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=10.0,
        duration_minutes=15.0,
        efficiency=3.2,
        energy_consumed=3.1,
    )
    await _dispatch_event(entity_id, state1, db_session)
    await db_session.flush()

    _, state2 = make_trip_event(
        device_id=_TEST_DEVICE_ID,
        distance_miles=25.0,   # different trip
        duration_minutes=40.0,
        efficiency=3.5,
        energy_consumed=7.1,
    )
    await _dispatch_event(entity_id, state2, db_session)
    await db_session.flush()

    rows = (await db_session.execute(
        select(EVTripMetrics).where(EVTripMetrics.device_id == _TEST_DEVICE_ID)
    )).scalars().all()
    assert len(rows) == 2
