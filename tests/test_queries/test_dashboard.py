"""Dashboard query layer validation tests.

Tests dashboard summary aggregation including total sessions, kWh, and cost.
"""

from datetime import UTC

import pytest

from web.queries.dashboard import query_dashboard_summary

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_dashboard_stats(db_session):
    """Create sessions with location_name matching network names for dashboard summary.

    Dashboard uses old-style compute_session_cost(s, networks_by_name) which
    resolves network via session.location_name -> networks_by_name dict lookup.
    """
    from datetime import datetime

    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVChargingNetwork

    db = db_session

    net = EVChargingNetwork(
        network_name="Dashboard Net",
        cost_per_kwh=0.40,
        is_free=False,
        is_verified=True,
    )
    db.add(net)
    await db.flush()

    # Sessions with location_name matching network name (old-style lookup)
    s1 = EVChargingSession(
        device_id="DASH_VIN",
        energy_kwh=50.0,
        location_name="Dashboard Net",
        session_start_utc=datetime(2025, 6, 1, tzinfo=UTC),
        is_complete=True,
        source_system="test",
    )
    s2 = EVChargingSession(
        device_id="DASH_VIN",
        energy_kwh=30.0,
        location_name="Dashboard Net",
        session_start_utc=datetime(2025, 6, 2, tzinfo=UTC),
        is_complete=True,
        source_system="test",
    )
    db.add_all([s1, s2])
    await db.flush()

    result = await query_dashboard_summary(db)

    assert result["total_sessions"] == 2
    assert result["total_kwh"] == pytest.approx(80.0, abs=0.01)
    # Cost: 50*0.40 + 30*0.40 = 20.00 + 12.00 = 32.00
    assert result["total_cost"] == pytest.approx(32.00, abs=0.01)
    assert result["avg_cost_per_session"] == pytest.approx(16.00, abs=0.01)
    assert result["avg_kwh_per_session"] == pytest.approx(40.00, abs=0.01)


async def test_dashboard_with_device_filter(db_session):
    """Verify dashboard stats filter by device_id correctly."""
    from datetime import datetime

    from db.models.charging_session import EVChargingSession

    db = db_session

    s = EVChargingSession(
        device_id="FILTER_VIN",
        energy_kwh=25.0,
        session_start_utc=datetime(2025, 6, 1, tzinfo=UTC),
        is_complete=True,
        source_system="test",
    )
    db.add(s)
    await db.flush()

    result = await query_dashboard_summary(db, device_id="FILTER_VIN")
    assert result["total_sessions"] == 1
    assert result["total_kwh"] == pytest.approx(25.0, abs=0.01)

    # Non-existent device returns zeros
    result_empty = await query_dashboard_summary(db, device_id="NONEXISTENT")
    assert result_empty["total_sessions"] == 0
    assert result_empty["total_kwh"] == 0.0


async def test_dashboard_empty(db_session):
    """No data -> zeros/None defaults."""
    result = await query_dashboard_summary(db_session)

    assert result["total_sessions"] == 0
    assert result["total_kwh"] == 0.0
    assert result["total_cost"] == 0.0
    assert result["avg_cost_per_session"] is None
    assert result["avg_kwh_per_session"] is None
