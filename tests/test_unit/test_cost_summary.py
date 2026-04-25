"""DB-backed tests for /charging/costs summary ratio helpers.
Covers `avg_cost_per_session`, `cost_per_mile`, `cost_per_kwh`, and
`free_charging_savings`. The critical invariant under test is the decision:
sessions with NULL `distance_added` are excluded from BOTH the numerator and
denominator of `cost_per_mile`. Empty denominators return `None` so the UI can
render `—` rather than `$0.00` or `NaN`.
"""

import pytest

from tests.factories.sessions import ChargingSessionFactory
from web.queries.costs import (
    avg_cost_per_session,
    cost_per_kwh,
    cost_per_mile,
    free_charging_savings,
)

pytestmark = pytest.mark.db


DEVICE_A = "TEST_VIN_A"
DEVICE_B = "TEST_VIN_B"


# ---------------------------------------------------------------------------
# avg_cost_per_session
# ---------------------------------------------------------------------------


async def test_avg_cost_per_session_empty_returns_none(db_session):
    """No sessions in range -> None (renders as em dash)."""
    result = await avg_cost_per_session(db_session, device_id=DEVICE_A, time_range="all")
    assert result is None


async def test_avg_cost_per_session_computes_mean(db_session):
    """Three sessions with costs [10, 20, 30] -> 20.0."""
    for c in (10.0, 20.0, 30.0):
        await ChargingSessionFactory.create(
            db_session,
            device_id=DEVICE_A,
            cost=c,
            energy_kwh=40.0,
            distance_added=100.0,
        )

    result = await avg_cost_per_session(db_session, device_id=DEVICE_A, time_range="all")
    assert result == pytest.approx(20.0, abs=0.001)


# ---------------------------------------------------------------------------
# cost_per_mile  (regression guard for D2 decision)
# ---------------------------------------------------------------------------


async def test_cost_per_mile_excludes_null_distance(db_session):
    """Session with NULL distance_added must be excluded from BOTH sides.

    - Session A: cost=50, distance_added=NULL  -> EXCLUDED
    - Session B: cost=25, distance_added=100 km -> INCLUDED

    Expected cost_per_mile = 25.0 / (100 * 0.621371)  ~= 0.40234
    """
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        cost=50.0,
        distance_added=None,
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        cost=25.0,
        distance_added=100.0,
    )

    result = await cost_per_mile(db_session, device_id=DEVICE_A, time_range="all")
    expected = 25.0 / (100.0 * 0.621371)
    assert result == pytest.approx(expected, rel=1e-4)


async def test_cost_per_mile_zero_distance_returns_none(db_session):
    """All usable sessions have distance_added=0 -> None (denominator zero)."""
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        cost=25.0,
        distance_added=0.0,
    )

    result = await cost_per_mile(db_session, device_id=DEVICE_A, time_range="all")
    assert result is None


# ---------------------------------------------------------------------------
# cost_per_kwh
# ---------------------------------------------------------------------------


async def test_cost_per_kwh_excludes_null_energy(db_session):
    """Null-energy session excluded from both numerator and denominator."""
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        cost=100.0,
        energy_kwh=None,
        distance_added=50.0,
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        cost=20.0,
        energy_kwh=50.0,
        distance_added=50.0,
    )

    result = await cost_per_kwh(db_session, device_id=DEVICE_A, time_range="all")
    # 20 / 50 = 0.40. The null-energy row must not inflate numerator.
    assert result == pytest.approx(0.40, abs=0.001)


# ---------------------------------------------------------------------------
# free_charging_savings
# ---------------------------------------------------------------------------


async def test_free_charging_savings_sums_is_free_sessions(db_session):
    """Sums `estimated_cost` across sessions flagged is_free=True."""
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        is_free=True,
        cost=0.0,
        estimated_cost=5.0,
        energy_kwh=20.0,
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        is_free=True,
        cost=0.0,
        estimated_cost=7.0,
        energy_kwh=28.0,
    )
    # Paid session — must NOT contribute.
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        is_free=False,
        cost=15.0,
        estimated_cost=15.0,
        energy_kwh=40.0,
    )

    result = await free_charging_savings(
        db_session, device_id=DEVICE_A, time_range="all"
    )
    assert result == pytest.approx(12.0, abs=0.001)


# ---------------------------------------------------------------------------
# device_id scoping
# ---------------------------------------------------------------------------


async def test_device_id_filter_honored(db_session):
    """Sessions split across two device_ids -> filter to one returns only its data."""
    # Device A: cost=10
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_A,
        cost=10.0,
        energy_kwh=40.0,
        distance_added=100.0,
    )
    # Device B: cost=200 — must not leak into Device A query
    await ChargingSessionFactory.create(
        db_session,
        device_id=DEVICE_B,
        cost=200.0,
        energy_kwh=80.0,
        distance_added=200.0,
    )

    result = await avg_cost_per_session(
        db_session, device_id=DEVICE_A, time_range="all"
    )
    assert result == pytest.approx(10.0, abs=0.001)
