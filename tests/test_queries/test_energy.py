"""Energy query layer validation tests.

Tests energy summary aggregation, charge type breakdown, and efficiency calculations.
"""

from datetime import UTC

import pytest

from web.queries.energy import query_energy_summary, query_monthly_energy

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_energy_summary_totals(energy_scenario):
    """energy_scenario -> query_energy_summary -> exact total kWh and session count."""
    db = energy_scenario["db"]
    exp = energy_scenario["expected"]

    result = await query_energy_summary(db, time_range="all")

    assert result["total_kwh"] == pytest.approx(exp["total_kwh"], abs=0.01)
    assert result["total_sessions"] == exp["total_sessions"]


async def test_energy_by_charge_type(energy_scenario):
    """Verify AC vs DC energy split matches known values."""
    db = energy_scenario["db"]
    exp = energy_scenario["expected"]

    result = await query_energy_summary(db, time_range="all")

    by_type = {item["charge_type"]: item for item in result["by_charge_type"]}

    assert "AC" in by_type
    assert by_type["AC"]["kwh"] == pytest.approx(exp["ac_kwh"], abs=0.01)
    assert by_type["AC"]["session_count"] == exp["ac_count"]

    assert "DC" in by_type
    assert by_type["DC"]["kwh"] == pytest.approx(exp["dc_kwh"], abs=0.01)
    assert by_type["DC"]["session_count"] == exp["dc_count"]


async def test_energy_efficiency_stats(energy_scenario):
    """Verify avg/best/worst efficiency calculations."""
    db = energy_scenario["db"]
    exp = energy_scenario["expected"]

    result = await query_energy_summary(db, time_range="all")

    assert result["avg_efficiency"] == pytest.approx(exp["avg_efficiency"], abs=0.01)
    assert result["best_efficiency"] == pytest.approx(exp["best_efficiency"], abs=0.01)
    assert result["worst_efficiency"] == pytest.approx(exp["worst_efficiency"], abs=0.01)


async def test_monthly_energy_aggregation(energy_scenario):
    """Verify monthly energy groups sessions by calendar month and charge type."""
    db = energy_scenario["db"]

    result = await query_monthly_energy(db, time_range="all")

    # Should have at least one entry
    assert len(result) >= 1
    # Each entry has month, charge_type, kwh
    for entry in result:
        assert "month" in entry
        assert "charge_type" in entry
        assert "kwh" in entry
        assert entry["kwh"] > 0

    # Total should match scenario total
    total = sum(entry["kwh"] for entry in result)
    assert total == pytest.approx(energy_scenario["expected"]["total_kwh"], abs=0.01)


async def test_energy_summary_custom_date_window(energy_scenario):
    """date_from/date_to bound the summary to the inclusive day window.

    energy_scenario DC sessions fall on 2025-05-31 through 2025-06-04 at 12:00 UTC.
    """
    db = energy_scenario["db"]

    result = await query_energy_summary(
        db, time_range="all", date_from="2025-05-31", date_to="2025-06-02",
    )

    # DC sessions on 05-31, 06-01, 06-02: 45 + 55 + 40 kWh
    assert result["total_sessions"] == 3
    assert result["total_kwh"] == pytest.approx(140.0, abs=0.01)


async def test_energy_summary_preset_wins_over_custom_dates(energy_scenario):
    """A non-'all' preset takes precedence over date_from/date_to."""
    db = energy_scenario["db"]

    # 7d window from "now" (2026) excludes all 2025 fixture sessions even
    # though the custom dates would match them.
    result = await query_energy_summary(
        db, time_range="7d", date_from="2025-05-31", date_to="2025-06-02",
    )

    assert result["total_sessions"] == 0


async def test_energy_summary_empty(db_session):
    """No sessions -> returns zeros/None gracefully."""
    result = await query_energy_summary(db_session, time_range="all")

    assert result["total_kwh"] == 0.0
    assert result["total_sessions"] == 0
    assert result["avg_efficiency"] is None
    assert result["best_efficiency"] is None
    assert result["worst_efficiency"] is None
    assert result["by_charge_type"] == []


async def test_phase_25_by_charge_type_includes_cost(db_session):
    """by_charge_type entries include a total_cost field summed from session.cost."""
    from datetime import datetime

    from db.models.charging_session import EVChargingSession
    from db.models.vehicle import EVVehicle

    device_id = "TEST_VIN_P25_COST"
    db_session.add(
        EVVehicle(
            device_id=device_id,
            display_name="Phase 25 Cost Vehicle",
            year=2024,
            make="Ford",
            model="F-150 Lightning",
            battery_capacity_kwh=131.0,
            vin=device_id,
            source_system="test_fixture",
        )
    )
    await db_session.flush()

    base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    db_session.add_all(
        [
            EVChargingSession(
                device_id=device_id,
                energy_kwh=20.0,
                charge_type="AC",
                cost=15.50,
                session_start_utc=base,
                is_complete=True,
                source_system="test_fixture",
            ),
            EVChargingSession(
                device_id=device_id,
                energy_kwh=40.0,
                charge_type="DC",
                cost=60.00,
                session_start_utc=base,
                is_complete=True,
                source_system="test_fixture",
            ),
        ]
    )
    await db_session.flush()

    result = await query_energy_summary(db_session, time_range="all", device_id=device_id)
    by_type = {item["charge_type"]: item for item in result["by_charge_type"]}

    assert "AC" in by_type and "DC" in by_type
    assert "total_cost" in by_type["AC"]
    assert by_type["AC"]["total_cost"] == pytest.approx(15.50, abs=0.01)
    assert by_type["DC"]["total_cost"] == pytest.approx(60.00, abs=0.01)
