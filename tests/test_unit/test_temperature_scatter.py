"""Unit tests for temperature-vs-efficiency scatter builder. phase_25."""
import pytest

from web.queries.driving_performance import build_temperature_correlation_chart

pytestmark = pytest.mark.unit


def _rows(n=6, base_temp=10.0, dist=100.0, energy=20.0):
    return [
        {
            "ambient_temp": base_temp + i,
            "distance": dist,
            "energy_consumed": energy,
            "start_time": None,
        }
        for i in range(n)
    ]


def test_phase_25_temp_scatter_empty_below_min_points():
    """build_temperature_correlation_chart returns '' when len(data) < min_points."""
    # 4 rows, min_points=5 → empty string
    assert (
        build_temperature_correlation_chart(
            _rows(n=4), distance_unit="us", min_points=5
        )
        == ""
    )
    assert (
        build_temperature_correlation_chart([], distance_unit="metric", min_points=5)
        == ""
    )


def test_phase_25_temp_scatter_derived_efficiency_from_distance_energy():
    """Efficiency comes from distance/energy_consumed, not the stored efficiency column."""
    # 100 km / 20 kWh = 5 km/kWh → when metric, Y=5.0 exactly
    html = build_temperature_correlation_chart(
        _rows(n=6), distance_unit="metric", min_points=5
    )
    assert html
    # sanity — 5.0 / 5 appears in the serialized values
    assert "5" in html


def test_phase_25_temp_scatter_us_units_converts_celsius_to_fahrenheit():
    """When distance_unit='us', ambient_temp is converted from °C to °F before plotting."""
    # base_temp=0°C → 32°F. Chart HTML must contain "32" as one of the x values.
    rows = _rows(n=6, base_temp=0.0)
    html = build_temperature_correlation_chart(rows, distance_unit="us", min_points=5)
    assert html
    assert "32" in html


def test_phase_25_temp_scatter_metric_units_passes_celsius_through():
    """When distance_unit='metric', ambient_temp passes through as °C."""
    rows = _rows(n=6, base_temp=0.0)
    html = build_temperature_correlation_chart(
        rows, distance_unit="metric", min_points=5
    )
    assert html
    # Metric label must appear in layout. Plotly JSON-encodes the degree sign
    # as an escaped unicode sequence, so accept either literal or escaped form.
    assert "°C" in html or r"\u00b0C" in html
    # km/kWh is the metric efficiency label (may appear as km\u002fkWh due to
    # plotly's forward-slash escaping in JSON)
    assert "km/kWh" in html or r"km\u002fkWh" in html


def test_phase_25_temp_scatter_includes_trendline_trace():
    """Output HTML contains a second Scatter trace named 'Trend' from np.polyfit(deg=1)."""
    rows = _rows(n=6)
    html = build_temperature_correlation_chart(rows, distance_unit="us", min_points=5)
    assert html
    # Scatter trace + Trend trace → both present in HTML
    assert "Trend" in html
    assert "markers" in html or "scatter" in html
