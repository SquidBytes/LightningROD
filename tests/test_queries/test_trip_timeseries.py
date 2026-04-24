"""Tests for trip time-series query functions and chart builders.
Covers: query_trip_battery_series, query_trip_vehicle_series,
_interpolate_series, build_drive_graph, build_environment_chart,
and the expanded chart builders.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from db.models.battery_status import EVBatteryStatus
from db.models.vehicle_status import EVVehicleStatus
from web.queries.trips import (
    _interpolate_series,
    build_drive_graph,
    build_environment_chart,
    build_expanded_battery_chart,
    build_expanded_driving_chart,
    build_expanded_environment_chart,
    query_trip_battery_series,
    query_trip_vehicle_series,
)

pytestmark = [pytest.mark.query, pytest.mark.db]

DEVICE_ID = "TEST_VIN_TS"
BASE_TIME = datetime(2025, 7, 1, 10, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# query_trip_battery_series
# ---------------------------------------------------------------------------


async def test_battery_series_returns_rows_in_window(db_session):
    """Records within the time window are returned; outside are excluded."""
    # Inside window
    for i in range(3):
        db_session.add(EVBatteryStatus(
            device_id=DEVICE_ID,
            recorded_at=BASE_TIME + timedelta(minutes=i * 10),
            hv_battery_soc=float(80 - i),
            hv_battery_range=float(240 - i * 5),
            hv_battery_kw=0.0,
            hv_battery_temperature=25.0,
            hv_battery_voltage=400.0,
            source_system="test",
        ))
    # Outside window (1 hour before start)
    db_session.add(EVBatteryStatus(
        device_id=DEVICE_ID,
        recorded_at=BASE_TIME - timedelta(hours=1),
        hv_battery_soc=50.0,
        source_system="test",
    ))
    await db_session.flush()

    end_time = BASE_TIME + timedelta(minutes=30)
    df = await query_trip_battery_series(db_session, DEVICE_ID, BASE_TIME, end_time)

    assert len(df) == 3
    assert list(df.columns) == ["time", "soc", "range", "kw", "battery_temp", "voltage"]
    # Ordered ascending by recorded_at
    assert df["soc"].iloc[0] == pytest.approx(80.0, abs=0.1)


async def test_battery_series_empty_when_no_data(db_session):
    """Returns empty DataFrame with correct columns when no records in window."""
    df = await query_trip_battery_series(
        db_session, DEVICE_ID, BASE_TIME, BASE_TIME + timedelta(hours=1)
    )

    assert df.empty
    assert set(df.columns) == {"time", "soc", "range", "kw", "battery_temp", "voltage"}


# ---------------------------------------------------------------------------
# query_trip_vehicle_series
# ---------------------------------------------------------------------------


async def test_vehicle_series_returns_rows_in_window(db_session):
    """Records within the time window are returned."""
    for i in range(4):
        db_session.add(EVVehicleStatus(
            device_id=DEVICE_ID,
            recorded_at=BASE_TIME + timedelta(minutes=i * 5),
            speed=float(30 + i * 5),
            outside_temperature=72.0,
            cabin_temperature=68.0,
            acceleration=0.1,
            source_system="test",
        ))
    # Outside window
    db_session.add(EVVehicleStatus(
        device_id=DEVICE_ID,
        recorded_at=BASE_TIME - timedelta(hours=2),
        speed=0.0,
        source_system="test",
    ))
    await db_session.flush()

    end_time = BASE_TIME + timedelta(minutes=20)
    df = await query_trip_vehicle_series(db_session, DEVICE_ID, BASE_TIME, end_time)

    assert len(df) == 4
    assert list(df.columns) == ["time", "speed", "outside_temp", "cabin_temp", "acceleration"]


async def test_vehicle_series_empty_when_no_data(db_session):
    """Returns empty DataFrame with correct columns when no records exist."""
    df = await query_trip_vehicle_series(
        db_session, DEVICE_ID, BASE_TIME, BASE_TIME + timedelta(hours=1)
    )

    assert df.empty
    assert set(df.columns) == {"time", "speed", "outside_temp", "cabin_temp", "acceleration"}


# ---------------------------------------------------------------------------
# _interpolate_series (unit-style, no DB)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_interpolate_fills_nan_linearly():
    """Linear interpolation fills NaN gaps in value columns."""
    times = [BASE_TIME + timedelta(minutes=i) for i in range(5)]
    df = pd.DataFrame({
        "time": times,
        "soc": [80.0, None, None, 74.0, 72.0],
    })

    result = _interpolate_series(df, ["soc"])

    # Middle values should be interpolated
    assert result["soc"].isna().sum() == 0
    assert result["soc"].iloc[1] == pytest.approx(78.0, abs=0.5)
    assert result["soc"].iloc[2] == pytest.approx(76.0, abs=0.5)
    # Boolean interpolated column created
    assert "soc_interpolated" in result.columns
    assert result["soc_interpolated"].iloc[1]  # was NaN → marked as interpolated
    assert not result["soc_interpolated"].iloc[0]  # had real value


@pytest.mark.unit
def test_interpolate_skips_wide_gaps():
    """Values across gaps wider than max_gap_minutes stay NaN."""
    times = [
        BASE_TIME,
        BASE_TIME + timedelta(minutes=1),
        BASE_TIME + timedelta(minutes=20),  # gap > 5 min
        BASE_TIME + timedelta(minutes=21),
    ]
    df = pd.DataFrame({
        "time": times,
        "speed": [30.0, None, None, 40.0],
    })

    result = _interpolate_series(df, ["speed"], max_gap_minutes=5)

    # Values in the wide gap span should not be interpolated (remain NaN)
    assert pd.isna(result["speed"].iloc[1]) or pd.isna(result["speed"].iloc[2])


@pytest.mark.unit
def test_interpolate_empty_df_returns_unchanged():
    """Empty DataFrame passes through without error."""
    df = pd.DataFrame(columns=["time", "soc"])
    result = _interpolate_series(df, ["soc"])
    assert result.empty


# ---------------------------------------------------------------------------
# Chart builders (unit-style, no DB)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_drive_graph_empty_returns_empty_string():
    empty_b = pd.DataFrame(columns=["time", "soc", "range", "kw", "battery_temp", "voltage"])
    empty_v = pd.DataFrame(columns=["time", "speed", "outside_temp", "cabin_temp", "acceleration"])
    assert build_drive_graph(empty_b, empty_v) == ""


@pytest.mark.unit
def test_build_environment_chart_empty_returns_empty_string():
    empty_v = pd.DataFrame(columns=["time", "speed", "outside_temp", "cabin_temp", "acceleration"])
    assert build_environment_chart(empty_v) == ""


@pytest.mark.unit
def test_build_drive_graph_with_data_returns_html():
    """Non-empty data produces an HTML string containing Plotly output."""
    times = [BASE_TIME + timedelta(minutes=i) for i in range(5)]
    battery_df = pd.DataFrame({
        "time": times,
        "soc": [80.0, 78.0, 76.0, 74.0, 72.0],
        "range": [240.0, 235.0, 230.0, 225.0, 220.0],
        "kw": [5.0, 5.0, 5.0, 5.0, 5.0],
        "battery_temp": [25.0] * 5,
        "voltage": [400.0] * 5,
    })
    vehicle_df = pd.DataFrame({
        "time": times,
        "speed": [40.0, 45.0, 50.0, 48.0, 42.0],
        "outside_temp": [72.0] * 5,
        "cabin_temp": [68.0] * 5,
        "acceleration": [0.1] * 5,
    })

    html = build_drive_graph(battery_df, vehicle_df)
    assert isinstance(html, str)
    assert len(html) > 100  # Not empty, contains Plotly HTML


@pytest.mark.unit
def test_build_environment_chart_with_data_returns_html():
    """Temperature data produces an HTML string."""
    times = [BASE_TIME + timedelta(minutes=i) for i in range(4)]
    vehicle_df = pd.DataFrame({
        "time": times,
        "speed": [30.0, 35.0, 40.0, 38.0],
        "outside_temp": [70.0, 71.0, 72.0, 71.0],
        "cabin_temp": [68.0, 68.0, 69.0, 68.0],
        "acceleration": [0.0] * 4,
    })

    html = build_environment_chart(vehicle_df)
    assert isinstance(html, str)
    assert len(html) > 100


@pytest.mark.unit
def test_build_expanded_battery_chart_empty_returns_empty_string():
    empty_b = pd.DataFrame(columns=["time", "soc", "range", "kw", "battery_temp", "voltage"])
    assert build_expanded_battery_chart(empty_b) == ""


@pytest.mark.unit
def test_build_expanded_driving_chart_empty_returns_empty_string():
    empty_v = pd.DataFrame(columns=["time", "speed", "outside_temp", "cabin_temp", "acceleration"])
    assert build_expanded_driving_chart(empty_v) == ""


@pytest.mark.unit
def test_build_expanded_environment_chart_empty_returns_empty_string():
    empty_v = pd.DataFrame(columns=["time", "speed", "outside_temp", "cabin_temp", "acceleration"])
    assert build_expanded_environment_chart(empty_v) == ""
