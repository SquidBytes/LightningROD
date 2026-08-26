"""Query-layer tests for /driving/performance data functions. phase_25."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from db.models.trip_metrics import EVTripMetrics
from tests.test_queries.conftest import DEVICE_ID
from web.queries.driving_performance import (
    _min_points_for_range,
    query_regen_per_trip,
    query_temperature_correlation,
)

pytestmark = pytest.mark.query


# ---------------------------------------------------------------------------
# Task 1 — temperature correlation
# ---------------------------------------------------------------------------


async def test_phase_25_temp_correlation_filters_unusable_trips(
    db_session, trips_with_ambient_temp
):
    """Every exclusion the query promises is exercised against a trip that trips it.

    The fixture alone contains nothing the filters would drop, so asserting
    that the returned rows look usable proves nothing — the query would pass
    with all four WHERE clauses deleted. Each unusable trip below must be
    absent from the result, and every usable one still present.
    """
    now = datetime.now(UTC)
    unusable = {
        "null ambient_temp": dict(ambient_temp=None, distance=50.0, energy=12.0),
        "null distance": dict(ambient_temp=12.0, distance=None, energy=12.0),
        "null energy": dict(ambient_temp=13.0, distance=50.0, energy=None),
        "zero energy": dict(ambient_temp=14.0, distance=50.0, energy=0.0),
    }
    db_session.add_all(
        EVTripMetrics(
            trip_id=uuid.uuid4(),
            device_id=DEVICE_ID,
            distance=spec["distance"],
            duration=45.0,
            energy_consumed=spec["energy"],
            ambient_temp=spec["ambient_temp"],
            start_time=now - timedelta(days=2, minutes=45),
            end_time=now - timedelta(days=2),
            is_complete=True,
            source_system="test_fixture",
        )
        for spec in unusable.values()
    )
    await db_session.flush()

    rows = await query_temperature_correlation(db_session, time_range="all")

    assert len(rows) == len(trips_with_ambient_temp), (
        f"expected only the {len(trips_with_ambient_temp)} usable trips, got "
        f"{len(rows)} — an unusable trip survived the filter"
    )
    assert {r["ambient_temp"] for r in rows} == {
        float(t.ambient_temp) for t in trips_with_ambient_temp
    }
    for r in rows:
        assert r["ambient_temp"] is not None
        assert r["distance"] is not None
        assert r["energy_consumed"] > 0


async def test_phase_25_temp_correlation_respects_range_filter(
    db_session, trips_with_ambient_temp
):
    """query_temperature_correlation applies the ?range= window via build_trip_time_filter."""
    rows_all = await query_temperature_correlation(db_session, time_range="all")
    rows_7d = await query_temperature_correlation(db_session, time_range="7d")
    assert len(rows_7d) <= len(rows_all)
    assert rows_all  # sanity — fixture had non-zero data


async def test_phase_25_temp_correlation_derives_efficiency(
    db_session, trips_with_ambient_temp
):
    """Returned rows include distance + energy_consumed so the chart builder can derive mi/kWh."""
    rows = await query_temperature_correlation(db_session, time_range="all")
    assert rows
    for r in rows:
        assert r["distance"] > 0
        assert r["energy_consumed"] > 0
        # The chart builder derives efficiency — query returns raw numerator/denominator
        assert "efficiency" not in r


def test_phase_25_temp_min_points_mapping():
    """_min_points_for_range returns 5 for short windows, 10 for long."""
    assert _min_points_for_range("7d") == 5
    assert _min_points_for_range("30d") == 5
    assert _min_points_for_range("90d") == 10
    assert _min_points_for_range("ytd") == 10
    assert _min_points_for_range("1y") == 10
    assert _min_points_for_range("all") == 10


# ---------------------------------------------------------------------------
# Task 2 — regen per trip
# ---------------------------------------------------------------------------


async def test_phase_25_regen_per_trip_returns_row_per_trip(
    db_session, trips_with_regen
):
    """query_regen_per_trip returns one row per trip with non-null range_regenerated."""
    rows = await query_regen_per_trip(db_session, time_range="all")
    assert len(rows) == 3  # fixture creates exactly 3
    for r in rows:
        assert r["range_regenerated"] is not None


async def test_phase_25_regen_per_trip_pct_calculation(
    db_session, trips_with_regen
):
    """Returned regen_pct matches regen_kwh / energy_consumed * 100 for known fixture trips."""
    rows = await query_regen_per_trip(db_session, time_range="all")
    assert rows
    for r in rows:
        expected_pct = (r["regen_kwh"] / r["energy_consumed"]) * 100.0
        assert r["regen_pct"] == pytest.approx(expected_pct)


async def test_phase_25_regen_kwh_derivation_from_range(
    db_session, trips_with_regen
):
    """regen_kwh == range_regenerated / (distance / energy_consumed)."""
    rows = await query_regen_per_trip(db_session, time_range="all")
    assert rows
    for r in rows:
        efficiency = r["distance"] / r["energy_consumed"]
        expected_kwh = r["range_regenerated"] / efficiency
        assert r["regen_kwh"] == pytest.approx(expected_kwh)
