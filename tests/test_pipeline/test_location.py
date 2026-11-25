"""record_position: measured GPS fixes, stored once, stamped with the fix's own time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.location import EVLocation
from web.services.pipeline.location import record_position
from web.services.sources.ford_metrics.samples import position_observation
from web.util.timestamps import utc

pytestmark = pytest.mark.db

DEVICE = "your_vin_000000001"
T0 = datetime(2026, 8, 24, 17, 0, tzinfo=UTC)


def _metrics(at: datetime, lat: float = 10.0, lon: float = 20.0, method: str = "ACTUAL") -> dict:
    return {
        "position": {
            "updateTime": at.isoformat(),
            "value": {"location": {"lat": lat, "lon": lon, "alt": 12.0}, "gpsCoordinateMethod": method},
        },
        "compassDirection": {"value": "NORTH"},
    }


async def _record(db, metrics: dict, source: str):
    return await record_position(db, position_observation(DEVICE, metrics, source))


async def _fixes(db):
    return (
        await db.execute(
            select(EVLocation).where(EVLocation.device_id == DEVICE).order_by(EVLocation.recorded_at)
        )
    ).scalars().all()


async def test_measured_fix_is_stored_with_its_own_time(db_session):
    await _record(db_session, _metrics(T0), "ha_fordpass")

    (fix,) = await _fixes(db_session)
    assert (utc(fix.recorded_at), utc(fix.original_timestamp)) == (T0, T0)
    assert (fix.latitude, fix.longitude, fix.altitude) == (10.0, 20.0, 12.0)
    assert fix.compass_direction == "NORTH"
    assert fix.source_system == "ha_fordpass"


async def test_estimated_position_is_not_stored(db_session):
    await _record(db_session, _metrics(T0, method="INFERRED"), "fcon")

    assert await _fixes(db_session) == []


async def test_same_fix_from_two_sources_is_stored_once(db_session):
    await _record(db_session, _metrics(T0), "fcon")
    await _record(db_session, _metrics(T0), "ha_fordpass")

    assert [f.source_system for f in await _fixes(db_session)] == ["fcon"]


async def test_fix_within_60_seconds_and_50_metres_is_skipped(db_session):
    await _record(db_session, _metrics(T0), "fcon")
    # 0.0001 degrees of latitude is about 11 metres.
    await _record(db_session, _metrics(T0 + timedelta(seconds=30), lat=10.0001), "fcon")

    assert len(await _fixes(db_session)) == 1


async def test_fix_within_60_seconds_but_far_away_is_kept(db_session):
    await _record(db_session, _metrics(T0), "fcon")
    # 0.001 degrees of latitude is about 111 metres.
    await _record(db_session, _metrics(T0 + timedelta(seconds=30), lat=10.001), "fcon")

    assert len(await _fixes(db_session)) == 2


async def test_fix_at_the_same_place_after_60_seconds_is_kept(db_session):
    await _record(db_session, _metrics(T0), "fcon")
    await _record(db_session, _metrics(T0 + timedelta(seconds=61)), "fcon")

    assert len(await _fixes(db_session)) == 2
