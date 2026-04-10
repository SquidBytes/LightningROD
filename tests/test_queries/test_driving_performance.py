"""Query-layer tests for /driving/performance data functions. phase_25."""
import pytest

from web.queries.driving_performance import (
    _min_points_for_range,
    query_regen_per_trip,
    query_temperature_correlation,
)

pytestmark = pytest.mark.query


# ---------------------------------------------------------------------------
# Task 1 — temperature correlation
# ---------------------------------------------------------------------------


async def test_phase_25_temp_correlation_filters_null_ambient_temp(
    db_session, trips_with_ambient_temp
):
    """query_temperature_correlation excludes trips where ambient_temp IS NULL."""
    rows = await query_temperature_correlation(db_session, time_range="all")
    assert rows
    for r in rows:
        assert r["ambient_temp"] is not None


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
