"""Cost query layer validation tests.

Tests cost summary aggregation, per-network breakdown, subscription savings,
location cost override cascade, and monthly cost grouping.
All assertions use EXACT values via pytest.approx.
"""

import pytest

from web.queries.costs import (
    compute_session_cost,
    query_cost_summary,
    query_monthly_costs,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


# ---------------------------------------------------------------------------
# Golden-path tests
# ---------------------------------------------------------------------------


async def test_cost_summary_totals(cost_scenario):
    """cost_scenario -> query_cost_summary -> exact total_cost and total_kwh."""
    db = cost_scenario["db"]
    exp = cost_scenario["expected"]

    result = await query_cost_summary(db, time_range="all")

    assert result["total_cost"] == pytest.approx(exp["total_cost"], abs=0.01)
    assert result["total_kwh"] == pytest.approx(exp["total_kwh"], abs=0.01)
    assert result["total_sessions"] == exp["total_sessions"]


async def test_cost_per_network(cost_scenario):
    """Verify per-network cost breakdown matches known values."""
    db = cost_scenario["db"]
    exp = cost_scenario["expected"]

    result = await query_cost_summary(db, time_range="all")

    by_network = {item["network"]: item for item in result["by_network"]}

    assert "Network A" in by_network
    assert by_network["Network A"]["total_cost"] == pytest.approx(exp["net_a_cost"], abs=0.01)
    assert by_network["Network A"]["session_count"] == exp["net_a_sessions"]

    assert "Network B" in by_network
    assert by_network["Network B"]["total_cost"] == pytest.approx(exp["net_b_cost"], abs=0.01)
    assert by_network["Network B"]["session_count"] == exp["net_b_sessions"]


async def test_free_sessions_counted(cost_scenario):
    """Verify free sessions are counted correctly."""
    db = cost_scenario["db"]
    exp = cost_scenario["expected"]

    result = await query_cost_summary(db, time_range="all")

    assert result["free_total_kwh"] == pytest.approx(exp["free_kwh"], abs=0.01)
    assert result["free_session_count"] == exp["free_count"]


async def test_subscription_savings(cost_scenario):
    """Verify subscription savings = non-member cost - member cost for active sub sessions."""
    db = cost_scenario["db"]
    exp = cost_scenario["expected"]

    result = await query_cost_summary(db, time_range="all")

    assert result["subscription_total_saved"] == pytest.approx(
        exp["subscription_savings"], abs=0.01
    )


async def test_cost_cascade_location_override(cost_scenario):
    """Location cost_per_kwh overrides network rate in cascade."""
    s5 = cost_scenario["sessions"][4]  # 25 kWh at Location X ($0.30/kWh)
    net_a = cost_scenario["net_a"]
    loc_x = cost_scenario["loc_x"]

    result = compute_session_cost(s5, network=net_a, location=loc_x)

    # Location override: 25 * 0.30 = 7.50
    assert result["display_cost"] == pytest.approx(7.50, abs=0.01)
    assert result["cost_source"] == "calculated"
    assert result["cost_per_kwh"] == pytest.approx(0.30, abs=0.001)


async def test_monthly_cost_breakdown(cost_scenario):
    """Verify monthly aggregation groups sessions by calendar month.

    NOTE: query_monthly_costs does NOT load subscription periods, so Network B
    sessions use the base rate ($0.45/kWh) regardless of subscription status.
    Expected monthly total differs from cost summary total.
    """
    db = cost_scenario["db"]

    result = await query_monthly_costs(db, time_range="all")

    # Should have entries for the months sessions fall in
    months = {entry["month"] for entry in result}
    assert len(months) >= 1  # At least one month present
    # Total across monthly data (no subscription applied):
    # s1: 17.50, s2: 10.50, s3: 40*0.45=18.00, s4: 9.00, s5: 7.50, s6: 0.00 = 62.50
    total = sum(entry["cost"] for entry in result)
    assert total == pytest.approx(62.50, abs=0.01)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_cost_summary_empty(db_session):
    """No sessions -> returns zeros/empty gracefully."""
    result = await query_cost_summary(db_session, time_range="all")

    assert result["total_cost"] == 0.0
    assert result["total_kwh"] == 0.0
    assert result["total_sessions"] == 0
    assert result["by_network"] == []


async def test_cost_no_subscription_period(cost_scenario):
    """Session outside subscription date range uses non-member rate."""
    s4 = cost_scenario["sessions"][3]  # 20 kWh on Network B, date 2025-04-15 (before sub start 2025-05-01)
    net_b = cost_scenario["net_b"]
    sub = cost_scenario["subscription"]

    result = compute_session_cost(
        s4, network=net_b, subscription_periods=[sub]
    )

    # Non-member rate: 20 * 0.45 = 9.00
    assert result["display_cost"] == pytest.approx(9.00, abs=0.01)
    assert result["subscription_active"] is False


async def test_cost_with_zero_energy(db_session):
    """Session with 0 kWh energy doesn't cause division errors."""
    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVChargingNetwork

    net = EVChargingNetwork(
        network_name="Zero Test Net",
        cost_per_kwh=0.35,
        is_free=False,
        is_verified=True,
    )
    db_session.add(net)
    await db_session.flush()

    s = EVChargingSession(
        device_id="TEST_ZERO",
        energy_kwh=0.0,
        network_id=net.id,
        session_start_utc=None,
        is_complete=True,
        source_system="test",
    )
    db_session.add(s)
    await db_session.flush()

    result = compute_session_cost(s, network=net)

    # 0 kWh * any rate = 0
    assert result["display_cost"] == pytest.approx(0.0, abs=0.001)
    assert result["actual_cost_per_kwh"] is None  # division by zero avoided
