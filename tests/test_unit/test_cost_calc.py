"""Pure function unit tests for cost calculation logic.

Tests the mathematical rules of the cost cascade and subscription savings
without any database dependency. Uses mock objects to simulate sessions,
networks, and locations.
"""

from datetime import UTC, date
from types import SimpleNamespace

import pytest

from web.queries.costs import (
    calculate_monthly_fees_in_range,
    compute_session_cost,
    find_active_subscription,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session(**kwargs):
    """Create a mock session object with sensible defaults."""
    defaults = {
        "energy_kwh": 50.0,
        "cost": None,
        "cost_source": None,
        "is_free": False,
        "location_name": None,
        "location_type": None,
        "network_id": None,
        "location_id": None,
        "session_start_utc": None,
        "estimated_cost": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _mock_network(**kwargs):
    """Create a mock network object."""
    defaults = {
        "id": 1,
        "network_name": "Test Network",
        "cost_per_kwh": 0.35,
        "is_free": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _mock_location(**kwargs):
    """Create a mock location object."""
    defaults = {
        "id": 1,
        "location_name": "Test Location",
        "cost_per_kwh": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _mock_subscription(**kwargs):
    """Create a mock subscription period."""
    defaults = {
        "member_rate": 0.25,
        "monthly_fee": 12.99,
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 12, 31),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# compute_session_cost tests
# ---------------------------------------------------------------------------


def test_cost_per_kwh_calculation():
    """Given known rate and energy, verify expected cost."""
    session = _mock_session(energy_kwh=40.0)
    network = _mock_network(cost_per_kwh=0.35)

    result = compute_session_cost(session, network=network)

    # 40.0 * 0.35 = 14.00
    assert result["display_cost"] == pytest.approx(14.00, abs=0.001)
    assert result["cost_source"] == "calculated"
    assert result["cost_per_kwh"] == pytest.approx(0.35, abs=0.001)


def test_free_session_flag():
    """Session with is_free=True -> display_cost = 0.0."""
    session = _mock_session(is_free=True, energy_kwh=50.0)
    network = _mock_network(cost_per_kwh=0.35)

    result = compute_session_cost(session, network=network)

    assert result["display_cost"] == 0.0
    assert result["is_free"] is True


def test_free_network_flag():
    """Network with is_free=True -> display_cost = 0.0."""
    session = _mock_session(energy_kwh=50.0)
    network = _mock_network(is_free=True, cost_per_kwh=0.0)

    result = compute_session_cost(session, network=network)

    assert result["display_cost"] == 0.0
    assert result["is_free"] is True


def test_stored_cost_takes_priority():
    """Session with stored cost (manual/imported) overrides calculated cost."""
    session = _mock_session(energy_kwh=50.0, cost=25.00, cost_source="imported")
    network = _mock_network(cost_per_kwh=0.35)

    result = compute_session_cost(session, network=network)

    assert result["display_cost"] == 25.00
    assert result["cost_source"] == "imported"


def test_location_override_takes_precedence():
    """Location cost_per_kwh overrides network rate in cascade."""
    session = _mock_session(energy_kwh=30.0)
    network = _mock_network(cost_per_kwh=0.45)
    location = _mock_location(cost_per_kwh=0.30)

    result = compute_session_cost(session, network=network, location=location)

    # 30.0 * 0.30 = 9.00
    assert result["display_cost"] == pytest.approx(9.00, abs=0.001)
    assert "location" in result["calculation"]


def test_subscription_member_rate():
    """Active subscription applies member_rate instead of network base rate."""
    from datetime import datetime

    session = _mock_session(
        energy_kwh=40.0,
        session_start_utc=datetime(2025, 6, 15, tzinfo=UTC),
    )
    network = _mock_network(cost_per_kwh=0.45)
    sub = _mock_subscription(member_rate=0.25)

    result = compute_session_cost(
        session, network=network, subscription_periods=[sub]
    )

    # 40.0 * 0.25 = 10.00
    assert result["display_cost"] == pytest.approx(10.00, abs=0.001)
    assert result["subscription_active"] is True
    assert "member" in result["calculation"]


def test_subscription_savings_formula():
    """Savings = non_member_cost - display_cost."""
    from datetime import datetime

    session = _mock_session(
        energy_kwh=40.0,
        session_start_utc=datetime(2025, 6, 15, tzinfo=UTC),
    )
    network = _mock_network(cost_per_kwh=0.45)
    sub = _mock_subscription(member_rate=0.25)

    result = compute_session_cost(
        session, network=network, subscription_periods=[sub]
    )

    # Non-member: 40 * 0.45 = 18.00, member: 40 * 0.25 = 10.00, savings: 8.00
    assert result["non_member_cost"] == pytest.approx(18.00, abs=0.001)
    assert result["savings"] == pytest.approx(8.00, abs=0.001)


def test_no_cost_data():
    """Session with no network/location -> display_cost is None."""
    session = _mock_session(energy_kwh=50.0)

    result = compute_session_cost(session)

    assert result["display_cost"] is None
    assert result["cost_source"] is None


# ---------------------------------------------------------------------------
# find_active_subscription tests
# ---------------------------------------------------------------------------


def test_find_active_subscription_found():
    """Date within subscription range -> returns subscription."""
    sub = _mock_subscription(start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))

    result = find_active_subscription([sub], date(2025, 6, 15))

    assert result is sub


def test_find_active_subscription_not_found():
    """Date outside all subscription ranges -> returns None."""
    sub = _mock_subscription(start_date=date(2025, 1, 1), end_date=date(2025, 3, 31))

    result = find_active_subscription([sub], date(2025, 6, 15))

    assert result is None


def test_find_active_subscription_open_ended():
    """Subscription with no end_date -> active for any date after start."""
    sub = _mock_subscription(start_date=date(2025, 1, 1), end_date=None)

    result = find_active_subscription([sub], date(2025, 12, 31))

    assert result is sub


# ---------------------------------------------------------------------------
# calculate_monthly_fees_in_range tests
# ---------------------------------------------------------------------------


def test_monthly_fees_single_month():
    """Period covering a single month -> one month fee."""
    sub = _mock_subscription(
        monthly_fee=12.99,
        start_date=date(2025, 6, 1),
        end_date=date(2025, 6, 30),
    )

    result = calculate_monthly_fees_in_range(
        [sub], date(2025, 6, 1), date(2025, 6, 30)
    )

    assert result == pytest.approx(12.99, abs=0.01)


def test_monthly_fees_three_months():
    """Period spanning 3 months -> 3 * monthly_fee."""
    sub = _mock_subscription(
        monthly_fee=10.00,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
    )

    result = calculate_monthly_fees_in_range(
        [sub], date(2025, 3, 1), date(2025, 5, 31)
    )

    assert result == pytest.approx(30.00, abs=0.01)


def test_monthly_fees_no_overlap():
    """Period outside range -> zero fees."""
    sub = _mock_subscription(
        monthly_fee=12.99,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 31),
    )

    result = calculate_monthly_fees_in_range(
        [sub], date(2025, 6, 1), date(2025, 12, 31)
    )

    assert result == 0.0
