"""Battery query layer validation tests.

Tests SOC timeline, charge curve, degradation trend, downsampling,
and charging region detection.
"""

from datetime import UTC, datetime, timedelta

import pytest

from web.queries.battery import (
    detect_charging_regions,
    query_charge_curve,
    query_degradation_data,
    query_soc_timeline,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


# ---------------------------------------------------------------------------
# Golden-path tests
# ---------------------------------------------------------------------------


async def test_soc_timeline(battery_scenario):
    """battery_scenario -> query_soc_timeline -> verify data points match known SOC values."""
    db = battery_scenario["db"]
    device_id = battery_scenario["device_id"]

    data = await query_soc_timeline(db, time_range="all", device_id=device_id)

    assert len(data) == battery_scenario["expected"]["record_count"]
    # Verify first and last SOC values
    assert data[0]["soc"] == pytest.approx(
        battery_scenario["expected"]["first_soc"], abs=0.1
    )
    assert data[-1]["soc"] == pytest.approx(
        battery_scenario["expected"]["last_soc"], abs=0.1
    )


async def test_charging_region_detection(battery_scenario):
    """Detect charging regions from battery data (kW < -0.5 threshold)."""
    db = battery_scenario["db"]
    device_id = battery_scenario["device_id"]

    data = await query_soc_timeline(db, time_range="all", device_id=device_id)

    regions = detect_charging_regions(data)

    # Expected: one charging region at indices 5-9
    assert len(regions) >= 1
    start_idx, end_idx = regions[0]
    # The region should span the indices where kw < -0.5
    assert start_idx == 5
    assert end_idx == 9


async def test_charge_curve_for_session(db_session):
    """Create a session + battery records during charging -> verify charge curve data."""
    from db.models.battery_status import EVBatteryStatus
    from db.models.charging_session import EVChargingSession
    from db.models.vehicle import EVVehicle

    db = db_session

    v = EVVehicle(
        device_id="CURVE_VIN", display_name="Curve Vehicle",
        vin="CURVE_VIN", source_system="test",
    )
    db.add(v)
    await db.flush()

    start = datetime(2025, 6, 10, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)

    session = EVChargingSession(
        device_id="CURVE_VIN",
        session_start_utc=start,
        session_end_utc=end,
        energy_kwh=30.0,
        start_soc=20.0,
        end_soc=55.0,
        charging_kw=50.0,
        max_power=75.0,
        is_complete=True,
        source_system="test",
    )
    db.add(session)
    await db.flush()

    # Create 5 battery records during the session
    for i in range(5):
        rec = EVBatteryStatus(
            device_id="CURVE_VIN",
            recorded_at=start + timedelta(minutes=i * 12),
            hv_battery_soc=20.0 + i * 8.75,
            hv_battery_kw=-50.0 + i * 5,
            source_system="test",
        )
        db.add(rec)
    await db.flush()

    result = await query_charge_curve(db, session_id=session.id)

    assert result["session"] is not None
    assert len(result["detailed"]) == 5
    assert result["detailed"][0]["soc"] == pytest.approx(20.0, abs=0.1)
    assert result["detailed"][-1]["soc"] == pytest.approx(55.0, abs=0.1)
    assert result["fallback"]["start_soc"] == pytest.approx(20.0, abs=0.1)
    assert result["fallback"]["end_soc"] == pytest.approx(55.0, abs=0.1)


async def test_degradation_trend(battery_scenario):
    """Verify degradation data returns daily max capacity values."""
    db = battery_scenario["db"]
    device_id = battery_scenario["device_id"]

    data = await query_degradation_data(db, time_range="all", device_id=device_id)

    # Should have at least 2 days of data (20 records over ~6.7 days)
    assert len(data) >= 2
    # First day max capacity should be 88.5
    assert data[0]["max_capacity"] == pytest.approx(
        battery_scenario["expected"]["max_capacity_day1"], abs=0.1
    )
    # All entries should have a date and max_capacity
    for entry in data:
        assert "date" in entry
        assert "max_capacity" in entry
        assert entry["max_capacity"] > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_soc_timeline_empty(db_session):
    """No battery records -> returns empty list."""
    data = await query_soc_timeline(db_session, time_range="all", device_id="NONEXISTENT")

    assert data == []


async def test_charge_curve_missing_session(db_session):
    """Non-existent session ID -> graceful empty result."""
    result = await query_charge_curve(db_session, session_id=99999)

    assert result["session"] is None
    assert result["detailed"] == []
    assert result["fallback"] is None


async def test_charge_curve_fallback_when_few_points(db_session):
    """Session with < 3 detailed battery points uses fallback data."""
    from db.models.charging_session import EVChargingSession

    session = EVChargingSession(
        device_id="FALLBACK_VIN",
        session_start_utc=datetime(2025, 6, 10, 10, 0, 0, tzinfo=UTC),
        session_end_utc=datetime(2025, 6, 10, 11, 0, 0, tzinfo=UTC),
        energy_kwh=20.0,
        start_soc=30.0,
        end_soc=60.0,
        charging_kw=40.0,
        max_power=55.0,
        is_complete=True,
        source_system="test",
    )
    db_session.add(session)
    await db_session.flush()

    result = await query_charge_curve(db_session, session_id=session.id)

    # No detailed records -> detailed is empty but fallback has session data
    assert len(result["detailed"]) < 3
    assert result["fallback"]["start_soc"] == pytest.approx(30.0, abs=0.1)
    assert result["fallback"]["end_soc"] == pytest.approx(60.0, abs=0.1)
    assert result["fallback"]["max_power"] == pytest.approx(55.0, abs=0.1)
