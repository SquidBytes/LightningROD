"""Batched vehicle status rows are dated when they were written, not read.

`handle_vehicle_status` accumulates fields into `_pending_vehicle_status` and
stamps `_recorded_at = datetime.now(UTC)` the moment the batch is opened,
discarding the `last_updated` each state carries. Live, the two are the same
instant.

The connect-time catch-up sweep replays every entity's current state, and a
parked car's states can be many hours old. Those readings are then filed
under the reconnect time. `_flush_battery_status` stamps the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.vehicle_status import EVVehicleStatus
from web.services.sources.ha_fordpass.dispatch import dispatch_slug

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

HA_CONFIG = {"unit_system": {"length": "km", "temperature": "°C"}}


def _state(vin: str, slug: str, value: str, when: datetime) -> dict:
    return {
        "entity_id": f"sensor.fordpass_{vin}_{slug}",
        "state": value,
        "attributes": {"unit_of_measurement": "km"} if slug == "odometer" else {},
        "last_changed": when.isoformat(),
        "last_updated": when.isoformat(),
    }


async def _batch_then_flush(db_session, vin: str, when: datetime) -> EVVehicleStatus:
    """Dispatch two telemetry states stamped `when`, then flush the batch."""
    for slug, value in (("odometer", "42123.0"), ("ignitionstatus", "OFF")):
        await dispatch_slug(
            f"sensor.fordpass_{vin}_{slug}",
            _state(vin, slug, value, when),
            HA_CONFIG,
            db_session,
            config_id=99,
        )
    # lastrefresh is what closes the batch and writes the row.
    await dispatch_slug(
        f"sensor.fordpass_{vin}_lastrefresh",
        _state(vin, "lastrefresh", when.isoformat(), when),
        HA_CONFIG,
        db_session,
        config_id=99,
    )
    await db_session.flush()
    return (
        await db_session.execute(
            select(EVVehicleStatus).where(EVVehicleStatus.device_id == vin)
        )
    ).scalar_one()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the pending batch stamps recorded_at with datetime.now() instead of "
        "the state's last_updated, so the catch-up sweep files a parked car's "
        "hours-old readings under the reconnect time"
    ),
)
async def test_stale_snapshot_state_keeps_its_own_timestamp(db_session):
    """A reading taken six hours ago belongs six hours ago on the timeline.

    Filing it under the reconnect time puts a stationary odometer and an
    hours-old ignition state at "now", which is what every trip and telemetry
    query reads as the vehicle's present condition.
    """
    six_hours_ago = datetime.now(UTC) - timedelta(hours=6)
    row = await _batch_then_flush(db_session, "STALEBATCH", six_hours_ago)

    drift_hours = (
        abs((row.recorded_at.replace(tzinfo=UTC) - six_hours_ago).total_seconds()) / 3600
    )
    assert drift_hours < 1, (
        f"reading from {six_hours_ago} was recorded at {row.recorded_at} "
        f"— {drift_hours:.1f} hours adrift"
    )


async def test_live_state_is_recorded_at_the_time_it_arrived(db_session):
    """The control: for a state that really is current, now() is correct."""
    now = datetime.now(UTC)
    row = await _batch_then_flush(db_session, "LIVEBATCH", now)

    drift = abs((row.recorded_at.replace(tzinfo=UTC) - now).total_seconds())
    assert drift < 60, f"live reading recorded {drift:.0f}s from its own timestamp"
