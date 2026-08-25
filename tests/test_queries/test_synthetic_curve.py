"""Query-layer tests for synthetic charge curve aggregation. phase_25 Wave 2."""
from datetime import UTC, datetime

import pytest

from db.models.charging_session import EVChargingSession
from web.queries.energy import (
    has_real_charge_curve_data,
    query_synthetic_curve_inputs,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_phase_25_dc_peak_aggregation_median(db_session, sessions_without_battery_status):
    """query_synthetic_curve_inputs returns median DC peak kW across DC sessions in range."""
    fx = sessions_without_battery_status
    exp = fx["expected"]

    result = await query_synthetic_curve_inputs(
        db_session, time_range="all", device_id=fx["device_id"]
    )
    assert result["dc_session_count"] == exp["dc_session_count"]
    assert result["median_peak_kw"] == pytest.approx(exp["median_peak_kw"], abs=0.01)


async def test_phase_25_fallback_trigger_hides_when_real_data_present(
    db_session, sessions_with_battery_status
):
    """When a DC session in window has >= 3 battery_status points, synthetic is NOT shown."""
    fx = sessions_with_battery_status
    assert (
        await has_real_charge_curve_data(
            db_session, time_range="all", device_id=fx["device_id"]
        )
        is True
    )


async def test_valueless_battery_rows_do_not_count_as_curve_data(
    db_session, sessions_with_valueless_battery_status
):
    """Rows with every value column NULL must not suppress the synthetic curve.

    A bare row count answered "did any row exist?" where it meant "do we have
    curve data?", so empty telemetry rendered a real curve built from nothing.
    """
    fx = sessions_with_valueless_battery_status
    assert (
        await has_real_charge_curve_data(
            db_session, time_range="all", device_id=fx["device_id"]
        )
        is False
    )


async def test_soc_without_pack_power_is_not_curve_data(
    db_session, sessions_with_soc_only_battery_status
):
    """kW is the curve's y-axis; SOC alone plots a flat line at zero."""
    fx = sessions_with_soc_only_battery_status
    assert (
        await has_real_charge_curve_data(
            db_session, time_range="all", device_id=fx["device_id"]
        )
        is False
    )


async def test_phase_25_fallback_trigger_shows_when_no_real_data(
    db_session, sessions_without_battery_status
):
    """When no DC session has >= 3 battery_status points, synthetic IS shown."""
    fx = sessions_without_battery_status
    assert (
        await has_real_charge_curve_data(
            db_session, time_range="all", device_id=fx["device_id"]
        )
        is False
    )


async def test_phase_25_dc_only_excludes_ac_sessions(
    db_session, sessions_without_battery_status
):
    """query_synthetic_curve_inputs excludes charge_type='AC' rows entirely."""
    fx = sessions_without_battery_status
    device_id = fx["device_id"]

    # Add an AC session with a huge peak — it must NOT affect median/count
    ac = EVChargingSession(
        device_id=device_id,
        charge_type="AC",
        energy_kwh=10.0,
        max_power=999.0,  # would skew median/count if included
        session_start_utc=datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC),
        session_end_utc=datetime(2025, 6, 1, 13, 0, 0, tzinfo=UTC),
        is_complete=True,
        source_system="test_fixture",
    )
    db_session.add(ac)
    await db_session.flush()

    result = await query_synthetic_curve_inputs(
        db_session, time_range="all", device_id=device_id
    )
    # Count unchanged (AC excluded)
    assert result["dc_session_count"] == fx["expected"]["dc_session_count"]
    assert result["median_peak_kw"] == pytest.approx(
        fx["expected"]["median_peak_kw"], abs=0.01
    )


async def test_phase_25_respects_range_window(
    db_session, sessions_without_battery_status
):
    """Aggregation respects the ?range= window (7d should be <= all)."""
    fx = sessions_without_battery_status
    result_all = await query_synthetic_curve_inputs(
        db_session, time_range="all", device_id=fx["device_id"]
    )
    result_7d = await query_synthetic_curve_inputs(
        db_session, time_range="7d", device_id=fx["device_id"]
    )
    assert result_7d["dc_session_count"] <= result_all["dc_session_count"]
