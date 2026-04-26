"""Tests for the performance summary sparkline query helpers.
Covers two new query functions added to web/queries/energy.py:
monthly_energy_series: (month_start, total_kwh) tuples per month.
efficiency_over_time_series: (date, km_per_kwh) tuples per session.
Unit base for efficiency matches the rest of energy.py: distance_added is stored
in km, energy_kwh in kWh, so efficiency is returned in km/kWh (metric base).
Route handlers apply unit conversion (MI_PER_KM) when rendering for US units.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.sessions import ChargingSessionFactory
from web.queries.energy import (
    efficiency_over_time_series,
    monthly_energy_series,
)


@pytest.mark.asyncio
async def test_monthly_energy_series_empty_returns_empty_list(db_session):
    """No sessions in DB → returns []."""
    result = await monthly_energy_series(db_session, time_range="all", device_id=None)
    assert result == []


@pytest.mark.asyncio
async def test_monthly_energy_series_buckets_by_month(db_session):
    """Three sessions across two calendar months → two rows with correct sums."""
    # Use two specific UTC months so the grouping is deterministic.
    jan = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    jan_2 = datetime(2026, 1, 22, 12, 0, 0, tzinfo=UTC)
    feb = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)

    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_A",
        session_start_utc=jan,
        session_end_utc=jan + timedelta(hours=1),
        energy_kwh=10.0,
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_A",
        session_start_utc=jan_2,
        session_end_utc=jan_2 + timedelta(hours=1),
        energy_kwh=15.0,
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_A",
        session_start_utc=feb,
        session_end_utc=feb + timedelta(hours=1),
        energy_kwh=7.5,
    )
    await db_session.flush()

    rows = await monthly_energy_series(db_session, time_range="all", device_id="VIN_A")
    assert len(rows) == 2
    # Ascending by month_start
    (m1, kwh1), (m2, kwh2) = rows
    assert m1 < m2
    # Jan total = 25.0, Feb total = 7.5
    assert kwh1 == pytest.approx(25.0)
    assert kwh2 == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_efficiency_over_time_series_excludes_nulls(db_session):
    """Sessions with null/zero energy or distance are excluded."""
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

    # Valid: both energy and distance present
    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_B",
        session_start_utc=base,
        session_end_utc=base + timedelta(hours=1),
        energy_kwh=10.0,
        distance_added=50.0,  # 50km / 10kWh = 5.0 km/kWh
    )
    # Excluded: NULL energy
    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_B",
        session_start_utc=base + timedelta(days=1),
        session_end_utc=base + timedelta(days=1, hours=1),
        energy_kwh=None,
        distance_added=40.0,
    )
    # Excluded: NULL distance
    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_B",
        session_start_utc=base + timedelta(days=2),
        session_end_utc=base + timedelta(days=2, hours=1),
        energy_kwh=12.0,
        distance_added=None,
    )
    await db_session.flush()

    rows = await efficiency_over_time_series(db_session, time_range="all", device_id="VIN_B")
    assert len(rows) == 1
    _date, eff = rows[0]
    # distance_added stored as km, returned in km/kWh (metric base)
    assert eff == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_efficiency_over_time_series_respects_device_id(db_session):
    """Filtering by device_id returns only that vehicle's rows."""
    base = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)

    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_ONE",
        session_start_utc=base,
        session_end_utc=base + timedelta(hours=1),
        energy_kwh=10.0,
        distance_added=50.0,
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id="VIN_TWO",
        session_start_utc=base + timedelta(days=1),
        session_end_utc=base + timedelta(days=1, hours=1),
        energy_kwh=20.0,
        distance_added=80.0,
    )
    await db_session.flush()

    rows_one = await efficiency_over_time_series(
        db_session, time_range="all", device_id="VIN_ONE"
    )
    assert len(rows_one) == 1

    rows_all = await efficiency_over_time_series(
        db_session, time_range="all", device_id=None
    )
    assert len(rows_all) == 2
