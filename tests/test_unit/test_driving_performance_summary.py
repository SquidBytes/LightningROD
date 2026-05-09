"""Regression tests for /driving/performance summary aggregations.

Locks the Total Regen (range) and Total Energy Regenerated (kWh) numbers
so a future refactor can't silently zero them out for live data.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.trips import TripFactory
from web.queries.driving_performance import (
    query_driving_performance_summary,
    query_regen_per_trip,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def device_id() -> str:
    return "TEST_VIN_REGEN"


def _trip_kwargs(seed: int, **extra) -> dict:
    """Return defaults sufficient for an EVTripMetrics insert."""
    return {
        "trip_id": uuid.uuid4(),
        "end_time": datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC) - timedelta(days=seed),
        "is_complete": True,
        "source_system": "test_factory",
        **extra,
    }


async def test_total_regen_sums_range_regenerated(db_session, device_id):
    """Three trips with range_regenerated populated → summary['total_regen'] is the sum."""
    for seed, regen_km in enumerate([3.0, 4.5, 5.0]):
        await TripFactory.create(
            db_session,
            **_trip_kwargs(
                seed,
                device_id=device_id,
                distance=50.0,
                energy_consumed=10.0,
                range_regenerated=regen_km,
            ),
        )
    await db_session.flush()

    summary = await query_driving_performance_summary(
        db_session, time_range="all", device_id=device_id
    )
    assert summary["total_regen"] == pytest.approx(12.5)
    assert summary["trip_count"] == 3


async def test_total_regen_none_when_no_regen_data(db_session, device_id):
    """Trips without range_regenerated → summary['total_regen'] is None (no card)."""
    await TripFactory.create(
        db_session,
        **_trip_kwargs(
            0, device_id=device_id, distance=10.0, energy_consumed=2.0,
            range_regenerated=None,
        ),
    )
    await db_session.flush()

    summary = await query_driving_performance_summary(
        db_session, time_range="all", device_id=device_id
    )
    assert summary["total_regen"] is None


async def test_query_regen_per_trip_derives_kwh(db_session, device_id):
    """range_regenerated / efficiency == regen_kwh (lock the formula)."""
    # 50 km / 10 kWh = 5 km/kWh efficiency; 4 km regen → 4/5 = 0.8 kWh
    await TripFactory.create(
        db_session,
        **_trip_kwargs(
            0, device_id=device_id, distance=50.0, energy_consumed=10.0,
            range_regenerated=4.0,
        ),
    )
    await db_session.flush()

    rows = await query_regen_per_trip(
        db_session, time_range="all", device_id=device_id
    )
    assert len(rows) == 1
    assert rows[0]["regen_kwh"] == pytest.approx(0.8)
